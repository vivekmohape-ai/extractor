from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import pdfplumber

from attendance_policy import calculate_payroll_attendance, normalize_status
from config import SUMMARY_COLUMNS


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


@dataclass
class ReportMetadata:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    days_in_month: int = 31
    date_range_text: Optional[str] = None
    pdf_name: Optional[str] = None
    company: Optional[str] = None
    department: Optional[str] = None
    report_name: str = "Monthly Status Report (Basic Report)"
    warnings: list[str] = field(default_factory=list)


@dataclass
class FailedRow:
    line_number: int
    raw_line: str
    reason: str

    @property
    def error(self) -> str:
        return self.reason

    def __getitem__(self, key: str) -> Any:
        if key in {"error", "reason"}:
            return self.reason
        return getattr(self, key)


@dataclass
class EmployeeRecord:
    employee_id: str
    employee_name: str
    days_present: float
    absent_count: float
    absent_days: list[int] = field(default_factory=list)
    source: str = "summary"
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    status_tokens: list[str] = field(default_factory=list)
    payroll_present_days: float = 0.0
    paid_policy_days: int = 0
    paid_holiday_days: int = 0
    paid_weekend_days: int = 0
    payroll_paid_dates: list[str] = field(default_factory=list)
    payroll_unpaid_absent_dates: list[str] = field(default_factory=list)
    active_start_day: Optional[int] = None
    active_end_day: Optional[int] = None
    raw_line: str = ""

    @property
    def daily_status_count(self) -> int:
        return len(self.status_tokens)

    @property
    def daily_status_complete(self) -> bool:
        return bool(self.active_start_day is not None and self.active_end_day is not None)

    def to_output_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "Employee ID": self.employee_id,
            "Employee Name": self.employee_name,
            "ESSL Present Days": self.days_present,
            "Payroll Present Days": self.payroll_present_days,
            "ESSL Absent Count": self.absent_count,
            "Payroll Unpaid Absent Days": len(self.payroll_unpaid_absent_dates),
            "Payroll Unpaid Absent Dates": ", ".join(self.payroll_unpaid_absent_dates),
            "ESSL Absent Days": ", ".join(str(d) for d in self.absent_days),
            "Active From": self.active_start_day,
            "Active To": self.active_end_day,
            "Policy Paid Days": self.paid_policy_days,
            "Public Holiday Paid Days": self.paid_holiday_days,
            "Weekend Paid Days": self.paid_weekend_days,
            "Payroll Paid Dates": ", ".join(self.payroll_paid_dates),
        }
        for idx, status in enumerate(self.status_tokens, start=1):
            row[f"Day {idx}"] = status
        return row


@dataclass
class ParseResult:
    records: list[EmployeeRecord]
    failed_rows: list[FailedRow]
    metadata: ReportMetadata
    employees_found: int = 0
    primary_count: int = 0
    fallback_count: int = 0

    @property
    def fallback_used(self) -> int:
        return self.fallback_count


def _parse_date_range(text: str) -> Optional[dict[str, Any]]:
    import re
    pattern = re.compile(
        r"(?P<sm>[A-Za-z]{3,9})\s+(?P<sd>\d{1,2})\s+(?P<sy>\d{4})\s+To\s+"
        r"(?P<em>[A-Za-z]{3,9})\s+(?P<ed>\d{1,2})\s+(?P<ey>\d{4})",
        re.I,
    )
    match = pattern.search(text or "")
    if not match:
        return None

    sm = MONTHS.get(match.group("sm").lower())
    em = MONTHS.get(match.group("em").lower())
    if sm is None or em is None:
        return None

    start = date(int(match.group("sy")), sm, int(match.group("sd")))
    end = date(int(match.group("ey")), em, int(match.group("ed")))
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "month": end.month,
        "year": end.year,
        "days_in_month": (end - date(end.year, end.month, 1)).days + 1,
        "date_range_text": match.group(0),
    }


def _metadata_from_page_texts(page_texts: list[str], pdf_name: Optional[str]) -> ReportMetadata:
    full_text = "\n".join(page_texts)
    meta = ReportMetadata(pdf_name=pdf_name)
    date_info = _parse_date_range(full_text)
    if date_info:
        meta.start_date = date_info["start_date"]
        meta.end_date = date_info["end_date"]
        meta.month = date_info["month"]
        meta.year = date_info["year"]
        meta.days_in_month = date_info["days_in_month"]
        meta.date_range_text = date_info["date_range_text"]

    for line in full_text.splitlines():
        cleaned = " ".join(line.split()).strip()
        lower = cleaned.lower()
        if lower.startswith("company:"):
            value = cleaned[len("Company:"):].strip()
            if "Printed On" in value:
                value = value.split("Printed On", 1)[0].strip()
            meta.company = value or None
            break

    for line in full_text.splitlines():
        cleaned = " ".join(line.split()).strip()
        lower = cleaned.lower()
        if lower.startswith("department "):
            meta.department = cleaned[len("Department "):].strip() or None
            break

    return meta


def _numeric(value: Any) -> float:
    text = str(value).strip().replace(",", "")
    return float(text)


def _parse_table_row(row: list[Any], meta: ReportMetadata) -> Optional[EmployeeRecord]:
    if len(row) < 10:
        return None
    if not str(row[0] or "").strip().isdigit():
        return None

    employee_id = str(row[1] or "").strip()
    name = str(row[2] or "").strip()
    if not employee_id or not name:
        return None

    summary_values = row[-7:]
    try:
        summary = {
            key: _numeric(value)
            for key, value in zip(SUMMARY_COLUMNS, summary_values)
        }
    except (TypeError, ValueError):
        return None

    day_cells = list(row[3:-7])
    daily_statuses = [normalize_status(value) for value in day_cells]

    active_days = [
        idx + 1
        for idx, status in enumerate(daily_statuses[:meta.days_in_month])
        if status
    ]
    active_start = min(active_days) if active_days else None
    active_end = max(active_days) if active_days else None

    policy = calculate_payroll_attendance(
        year=meta.year or 2000,
        month=meta.month or 1,
        daily_statuses=daily_statuses,
        active_start_day=active_start,
        active_end_day=active_end,
    )

    absent_days = [
        idx + 1
        for idx, status in enumerate(daily_statuses[:meta.days_in_month])
        if status == "A"
    ]

    warnings: list[str] = []
    if len(daily_statuses) != meta.days_in_month:
        warnings.append(
            f"Daily table has {len(daily_statuses)} cells; expected {meta.days_in_month}."
        )

    return EmployeeRecord(
        employee_id=employee_id,
        employee_name=name,
        days_present=int(summary["P"]) if float(summary["P"]).is_integer() else summary["P"],
        absent_count=int(summary["A"]) if float(summary["A"]).is_integer() else summary["A"],
        absent_days=absent_days,
        source="table",
        warnings=warnings,
        summary=summary,
        status_tokens=daily_statuses,
        payroll_present_days=policy["payroll_present_days"],
        paid_policy_days=policy["paid_policy_days"],
        paid_holiday_days=policy["paid_holiday_days"],
        paid_weekend_days=policy["paid_weekend_days"],
        payroll_paid_dates=policy["paid_dates"],
        payroll_unpaid_absent_dates=policy["unpaid_absent_dates"],
        active_start_day=active_start,
        active_end_day=active_end,
    )


def parse_report(pdf_bytes: bytes, pdf_name: Optional[str] = None) -> ParseResult:
    if not pdf_bytes:
        raise ValueError("PDF input is empty.")

    records: list[EmployeeRecord] = []
    failed_rows: list[FailedRow] = []
    page_texts: list[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            page_texts.append(text)

            tables = page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                    "intersection_tolerance": 3,
                }
            )

            for table in tables:
                for row in table[1:]:
                    try:
                        record = _parse_table_row(row, _metadata_from_page_texts(page_texts, pdf_name))
                    except Exception as exc:
                        record = None
                        failed_rows.append(
                            FailedRow(
                                line_number=page_number,
                                raw_line=" | ".join(str(v or "") for v in row),
                                reason=str(exc),
                            )
                        )

                    if record:
                        records.append(record)

    meta = _metadata_from_page_texts(page_texts, pdf_name)
    if meta.month is None or meta.year is None:
        meta.warnings.append("Report month/year could not be extracted.")

    # Avoid duplicate rows if a page exposes more than one table.
    dedup: dict[str, EmployeeRecord] = {}
    for record in records:
        dedup[record.employee_id] = record
    records = list(dedup.values())

    return ParseResult(
        records=records,
        failed_rows=failed_rows,
        metadata=meta,
        employees_found=len(records) + len(failed_rows),
        primary_count=len(records),
        fallback_count=0,
    )
