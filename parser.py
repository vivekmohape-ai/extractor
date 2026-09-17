"""
ESSL Monthly Status Report parser.

Primary attendance source:
    ESSL machine generated summary columns.

Fallback attendance source:
    Extracted daily attendance statuses.

Important:
    pdfplumber does not reliably preserve empty PDF table cells.
    Therefore, this parser does not require exactly 31 daily status
    tokens to be present in the extracted text.

The parser uses the stable structure of each ESSL employee row:

    Sl Emp.Code Name <daily statuses> P A L H HP WO WOP

The final seven numeric values are treated as the authoritative
machine generated summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import io

import pdfplumber

from config import (
    ABSENT_STATUS,
    FALLBACK_STATUS_WEIGHTS,
    SUMMARY_COLUMNS,
)
from utils import (
    calculate_absent_days,
    calculate_fallback_present,
    extract_date_range,
    normalize_status,
    split_employee_row,
)


@dataclass
class ReportMetadata:
    """Metadata extracted from the ESSL report."""

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    days_in_month: int = 31
    date_range_text: Optional[str] = None
    pdf_name: Optional[str] = None


@dataclass
class FailedRow:
    """A row that appeared to be an employee row but could not be parsed."""

    line_number: int
    raw_line: str
    reason: str


@dataclass
class EmployeeRecord:
    """
    Normalized employee attendance record.

    This class intentionally keeps compatibility with excel_writer.py,
    which expects a to_output_row() method.
    """

    employee_id: str
    employee_name: str

    days_present: float
    absent_count: float

    absent_days: List[int] = field(default_factory=list)

    source: str = "summary"

    warnings: List[str] = field(default_factory=list)

    summary: Dict[str, Any] = field(default_factory=dict)

    status_tokens: List[str] = field(default_factory=list)

    daily_status_count: int = 0

    daily_status_complete: bool = False

    raw_line: str = ""

    def to_output_row(self) -> Dict[str, Any]:
        """
        Convert the record into the structure expected by excel_writer.py.
        """
        absent_days_text = ", ".join(
            str(day)
            for day in self.absent_days
        )

        return {
            "Employee ID": self.employee_id,
            "Employee Name": self.employee_name,
            "Days Present": self.days_present,
            "Absent Count": self.absent_count,
            "Absent Days": absent_days_text,
        }


@dataclass
class ParseResult:
    """Result returned by parse_report()."""

    records: List[EmployeeRecord]

    failed_rows: List[FailedRow]

    metadata: ReportMetadata

    employees_found: int = 0

    fallback_used: int = 0

    @property
    def successfully_parsed(self) -> int:
        return len(self.records)

    @property
    def failed_count(self) -> int:
        return len(self.failed_rows)


def _normalize_number(value: Any) -> float | int:
    """Convert 20.0 to 20 while preserving values such as 13.5."""
    numeric = float(value)

    if numeric.is_integer():
        return int(numeric)

    return numeric


def _looks_like_employee_row(line: str) -> bool:
    """
    Quick structural check.

    ESSL employee rows start with:

        Sl
        Emp Code
    """
    if not line:
        return False

    tokens = line.split()

    if len(tokens) < 2:
        return False

    first = tokens[0]
    second = tokens[1]

    if not first.isdigit():
        return False

    try:
        float(second)
        return True
    except (TypeError, ValueError):
        return False


def _is_summary_valid(
    summary: Dict[str, Any],
    days_in_month: int,
) -> bool:
    """
    Validate the ESSL machine summary.

    The summary columns are:

        P A L H HP WO WOP

    P and A cannot be negative and cannot exceed the number of
    calendar days in the reporting month.
    """
    for column in SUMMARY_COLUMNS:
        if column not in summary:
            return False

        try:
            value = float(summary[column])
        except (TypeError, ValueError):
            return False

        if value < 0:
            return False

    try:
        present = float(summary["P"])
        absent = float(summary["A"])
    except (TypeError, ValueError):
        return False

    if present > days_in_month:
        return False

    if absent > days_in_month:
        return False

    return True


def _daily_summary_matches_machine_summary(
    status_tokens: List[str],
    summary: Dict[str, Any],
) -> bool:
    """
    Compare daily derived present days with the ESSL P summary.

    This check is meaningful only when a complete daily sequence
    was extracted.
    """
    if not status_tokens:
        return False

    derived_present = calculate_fallback_present(
        status_tokens,
        FALLBACK_STATUS_WEIGHTS,
    )

    machine_present = float(
        summary["P"]
    )

    return abs(
        derived_present - machine_present
    ) < 0.01


def _parse_row(
    row_data: Dict[str, Any],
    metadata: ReportMetadata,
) -> EmployeeRecord:
    """
    Convert split_employee_row() output into EmployeeRecord.

    Attendance priority:

        1. ESSL machine summary
        2. Daily status fallback
    """

    employee_id = str(
        row_data["employee_id"]
    ).strip()

    employee_name = str(
        row_data["employee_name"]
    ).strip()

    raw_status_tokens = row_data.get(
        "status_tokens",
        [],
    )

    status_tokens: List[str] = []

    for raw_status in raw_status_tokens:
        normalized = normalize_status(
            raw_status
        )

        if normalized is not None:
            status_tokens.append(
                normalized
            )

    summary = row_data.get(
        "summary",
        {},
    )

    warnings: List[str] = []

    daily_status_count = len(
        status_tokens
    )

    daily_status_complete = (
        daily_status_count
        == metadata.days_in_month
    )

    # ---------------------------------------------------------
    # PRIMARY SOURCE: ESSL MACHINE SUMMARY
    # ---------------------------------------------------------

    if _is_summary_valid(
        summary,
        metadata.days_in_month,
    ):
        days_present = _normalize_number(
            summary["P"]
        )

        absent_count = _normalize_number(
            summary["A"]
        )

        source = "summary"

        # We can derive exact absent day numbers only if all
        # daily cells survived PDF text extraction.
        absent_days = calculate_absent_days(
            status_tokens,
            days_in_month=metadata.days_in_month,
            complete_only=True,
        )

        if not daily_status_complete:
            warnings.append(
                "Daily attendance cells were incomplete after PDF "
                "text extraction. ESSL summary P/A values were used "
                "as authoritative attendance totals. Exact absence "
                "dates were not derived."
            )

        else:
            if not _daily_summary_matches_machine_summary(
                status_tokens,
                summary,
            ):
                warnings.append(
                    "Extracted daily attendance did not exactly "
                    "match the ESSL machine summary P value. "
                    "The ESSL machine summary was retained as "
                    "authoritative."
                )

    # ---------------------------------------------------------
    # FALLBACK SOURCE: DAILY STATUS TOKENS
    # ---------------------------------------------------------

    else:
        if not status_tokens:
            raise ValueError(
                "ESSL summary is invalid and no daily attendance "
                "status tokens were available."
            )

        days_present = _normalize_number(
            calculate_fallback_present(
                status_tokens,
                FALLBACK_STATUS_WEIGHTS,
            )
        )

        absent_days = calculate_absent_days(
            status_tokens,
            days_in_month=metadata.days_in_month,
            complete_only=True,
        )

        if daily_status_complete:
            absent_count = len(
                absent_days
            )
        else:
            absent_count = sum(
                1
                for status in status_tokens
                if status in ABSENT_STATUS
            )

        source = "daily_fallback"

        warnings.append(
            "ESSL machine summary was invalid. Attendance "
            "was calculated from extracted daily status tokens."
        )

        if not daily_status_complete:
            warnings.append(
                "Daily attendance sequence was incomplete after "
                "PDF text extraction, so exact absence dates "
                "could not be reliably determined."
            )

    return EmployeeRecord(
        employee_id=employee_id,
        employee_name=employee_name,
        days_present=days_present,
        absent_count=absent_count,
        absent_days=absent_days,
        source=source,
        warnings=warnings,
        summary={
            key: _normalize_number(value)
            for key, value in summary.items()
        },
        status_tokens=status_tokens,
        daily_status_count=daily_status_count,
        daily_status_complete=daily_status_complete,
        raw_line=str(
            row_data.get(
                "raw_line",
                "",
            )
        ),
    )


def _extract_metadata(
    full_text: str,
    pdf_name: Optional[str],
) -> ReportMetadata:
    """Extract report metadata from the complete PDF text."""

    metadata = ReportMetadata(
        pdf_name=pdf_name,
        days_in_month=31,
    )

    date_info = extract_date_range(
        full_text
    )

    if date_info:
        metadata.start_date = date_info.get(
            "start_date"
        )

        metadata.end_date = date_info.get(
            "end_date"
        )

        metadata.month = date_info.get(
            "month"
        )

        metadata.year = date_info.get(
            "year"
        )

        metadata.days_in_month = int(
            date_info.get(
                "days_in_month",
                31,
            )
        )

        metadata.date_range_text = date_info.get(
            "date_range_text"
        )

    return metadata


def parse_report(
    pdf_bytes: bytes,
    pdf_name: Optional[str] = None,
) -> ParseResult:
    """
    Parse an ESSL Monthly Status Report PDF.

    The parser scans all pages and accepts employee rows even when
    blank daily PDF cells were omitted by text extraction.
    """

    if not pdf_bytes:
        raise ValueError(
            "PDF input is empty."
        )

    records: List[EmployeeRecord] = []

    failed_rows: List[FailedRow] = []

    page_texts: List[str] = []

    try:
        with pdfplumber.open(
            io.BytesIO(pdf_bytes)
        ) as pdf:

            for page in pdf.pages:

                try:
                    text = page.extract_text(
                        x_tolerance=2,
                        y_tolerance=3,
                    )

                except Exception:
                    text = page.extract_text()

                if text:
                    page_texts.append(
                        text
                    )

    except Exception as exc:
        raise RuntimeError(
            f"Unable to read PDF: {exc}"
        ) from exc

    if not page_texts:
        raise ValueError(
            "No extractable text was found in the PDF."
        )

    full_text = "\n".join(
        page_texts
    )

    metadata = _extract_metadata(
        full_text=full_text,
        pdf_name=pdf_name,
    )

    line_number = 0

    for page_text in page_texts:

        for raw_line in page_text.splitlines():

            line_number += 1

            line = " ".join(
                raw_line.split()
            ).strip()

            if not line:
                continue

            lower_line = line.lower()

            # Ignore report headers and footer/header fragments.
            if (
                "monthly status report" in lower_line
                or "department default" in lower_line
                or "sl emp. code" in lower_line
                or "generated by:essl" in lower_line
                or "generated by: essl" in lower_line
                or lower_line.startswith("company:")
                or "page no." in lower_line
            ):
                continue

            # Only process lines that look like employee rows.
            if not _looks_like_employee_row(
                line
            ):
                continue

            row_data = split_employee_row(
                line=line,
                days_in_month=metadata.days_in_month,
            )

            if row_data is None:
                failed_rows.append(
                    FailedRow(
                        line_number=line_number,
                        raw_line=line,
                        reason=(
                            "Could not identify the employee "
                            "name and trailing ESSL summary."
                        ),
                    )
                )
                continue

            try:
                record = _parse_row(
                    row_data=row_data,
                    metadata=metadata,
                )

                records.append(
                    record
                )

            except Exception as exc:
                failed_rows.append(
                    FailedRow(
                        line_number=line_number,
                        raw_line=line,
                        reason=str(exc),
                    )
                )

    fallback_used = sum(
        1
        for record in records
        if record.source == "daily_fallback"
    )

    employees_found = (
        len(records)
        + len(failed_rows)
    )

    return ParseResult(
        records=records,
        failed_rows=failed_rows,
        metadata=metadata,
        employees_found=employees_found,
        fallback_used=fallback_used,
    )
