"""
ESSL Monthly Status Report parser.

Primary source of attendance:
    ESSL machine generated P summary.

Fallback source:
    Extracted daily attendance statuses.

The parser is intentionally tolerant of missing daily PDF cells because
pdfplumber may omit blank cells when converting the ESSL table to text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pdfplumber

from config import (
    ABSENT_STATUS,
    FALLBACK_WEIGHTS,
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
    """
    Metadata extracted from the ESSL report.
    """

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    days_in_month: int = 31
    date_range_text: Optional[str] = None
    pdf_name: Optional[str] = None


@dataclass
class FailedRow:
    """
    A row that looked like an employee row but could not be parsed.
    """

    line_number: int
    raw_line: str
    reason: str


@dataclass
class EmployeeRecord:
    """
    Normalized employee attendance record.
    """

    employee_id: str
    employee_name: str

    days_present: float
    absent_count: float

    absent_days: List[int] = field(default_factory=list)

    source: str = "summary"

    warnings: List[str] = field(default_factory=list)

    # Additional information useful for validation/debugging.
    summary: Dict[str, Any] = field(default_factory=dict)

    status_tokens: List[str] = field(default_factory=list)

    daily_status_count: int = 0

    daily_status_complete: bool = False

    raw_line: str = ""


@dataclass
class ParseResult:
    """
    Result returned by parse_report().
    """

    records: List[EmployeeRecord]
    failed_rows: List[FailedRow]
    metadata: ReportMetadata

    # Useful counters exposed directly for the Streamlit UI.
    employees_found: int = 0
    fallback_used: int = 0

    @property
    def successfully_parsed(self) -> int:
        return len(self.records)

    @property
    def failed_count(self) -> int:
        return len(self.failed_rows)


def _normalize_number(value: Any) -> float | int:
    """
    Convert numeric values into a cleaner int/float representation.
    """
    numeric = float(value)

    if numeric.is_integer():
        return int(numeric)

    return numeric


def _looks_like_employee_row(line: str) -> bool:
    """
    Quick structural check.

    ESSL employee rows begin with:
        Sl
        Emp Code
    """
    if not line:
        return False

    tokens = line.split()

    if len(tokens) < 2:
        return False

    return (
        tokens[0].isdigit()
        and tokens[1].replace(".", "", 1).isdigit()
    )


def _is_summary_valid(
    summary: Dict[str, Any],
    days_in_month: int,
) -> bool:
    """
    Validate the ESSL machine summary.

    We primarily trust the ESSL summary when its values are
    structurally valid.

    P and A must be non negative.

    P should never exceed the number of calendar days in the month.

    A should never exceed the number of calendar days in the month.

    Other summary values are also required to be non negative.
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
    Compare the extracted daily status sequence to the machine
    generated P summary.

    This is only used when the daily sequence is complete.

    For incomplete sequences, the comparison is not meaningful and
    therefore returns True rather than treating missing PDF cells as
    a mismatch.
    """
    if not status_tokens:
        return False

    derived_present = calculate_fallback_present(
        status_tokens,
        FALLBACK_WEIGHTS,
    )

    machine_present = float(summary["P"])

    return abs(
        derived_present - machine_present
    ) < 0.01


def _parse_row(
    row_data: Dict[str, Any],
    metadata: ReportMetadata,
) -> EmployeeRecord:
    """
    Convert split_employee_row() output into EmployeeRecord.

    Attendance source priority:

        1. ESSL summary P
        2. daily status fallback only when summary is invalid
    """
    employee_id = str(
        row_data["employee_id"]
    ).strip()

    employee_name = str(
        row_data["employee_name"]
    ).strip()

    status_tokens = [
        normalize_status(status)
        for status in row_data.get(
            "status_tokens",
            [],
        )
    ]

    status_tokens = [
        status
        for status in status_tokens
        if status is not None
    ]

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
    # PRIMARY SOURCE
    # ---------------------------------------------------------
    #
    # Use the machine generated P summary whenever valid.
    #
    # Example:
    #     ... 21 2 0 0 0 6 0
    #
    # This means:
    #     P   = 21
    #     A   = 2
    #     L   = 0
    #     H   = 0
    #     HP  = 0
    #     WO  = 6
    #     WOP = 0
    #
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

        # -----------------------------------------------------
        # Absence dates
        # -----------------------------------------------------
        #
        # We only derive exact absence day numbers if all
        # daily cells were preserved.
        #
        # For late joiners the PDF text extraction can omit
        # leading blank cells. Returning shifted absence dates
        # would be worse than returning no dates.
        #
        absent_days = calculate_absent_days(
            status_tokens,
            days_in_month=metadata.days_in_month,
            complete_only=True,
        )

        if not daily_status_complete:
            warnings.append(
                "Daily attendance cells were incomplete after PDF "
                "text extraction. ESSL summary P/A values were used "
                "as the authoritative attendance totals. Exact "
                "absence dates were not derived."
            )

        else:
            # Complete sequence available.
            # Compare daily derived present to machine summary.
            if not _daily_summary_matches_machine_summary(
                status_tokens,
                summary,
            ):
                warnings.append(
                    "Daily attendance total did not exactly match "
                    "the ESSL machine summary P value. The ESSL "
                    "machine summary was retained as authoritative."
                )

    # ---------------------------------------------------------
    # FALLBACK SOURCE
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
                FALLBACK_WEIGHTS,
            )
        )

        absent_days = calculate_absent_days(
            status_tokens,
            days_in_month=metadata.days_in_month,
            complete_only=True,
        )

        # Since the status list is potentially incomplete,
        # do not pretend the derived absence count is complete.
        if daily_status_complete:
            absent_count = len(absent_days)
        else:
            # Summary is invalid and exact A count cannot be
            # trusted. Count only the extracted A tokens.
            absent_count = sum(
                1
                for status in status_tokens
                if status in ABSENT_STATUS
            )

            warnings.append(
                "ESSL machine summary was invalid. Attendance was "
                "calculated from extracted daily statuses. The "
                "daily status sequence was incomplete, so absence "
                "dates may not be fully recoverable."
            )

        source = "daily_fallback"

        warnings.append(
            "Attendance totals were calculated from daily "
            "attendance status tokens because the ESSL summary "
            "could not be validated."
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
    """
    Extract report metadata from the complete PDF text.
    """
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

    Args:
        pdf_bytes:
            PDF file contents as bytes.

        pdf_name:
            Original uploaded filename.

    Returns:
        ParseResult

    Parsing strategy:
        1. Extract text from every page using pdfplumber.
        2. Identify employee-like rows.
        3. Parse the stable first two tokens.
        4. Parse the stable final seven numeric summary values.
        5. Treat the middle section as variable length.
        6. Use ESSL P summary as authoritative Days Present.
        7. Use daily statuses for validation and absence dates
           only when complete.
    """
    if not pdf_bytes:
        raise ValueError(
            "PDF input is empty."
        )

    records: List[EmployeeRecord] = []
    failed_rows: List[FailedRow] = []

    page_texts: List[str] = []

    try:
        import io

        with pdfplumber.open(
            io.BytesIO(pdf_bytes)
        ) as pdf:
            for page in pdf.pages:
                try:
                    text = page.extract_text(
                        x_tolerance=2,
                        y_tolerance=3,
                    )

                    if text:
                        page_texts.append(text)

                except Exception:
                    # Try normal extraction if tuned extraction fails.
                    text = page.extract_text()

                    if text:
                        page_texts.append(text)

    except Exception as exc:
        raise RuntimeError(
            f"Unable to read PDF: {exc}"
        ) from exc

    if not page_texts:
        raise ValueError(
            "No extractable text was found in the PDF."
        )

    full_text = "\n".join(page_texts)

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

            # Skip all obvious headers and footers quickly.
            lower_line = line.lower()

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

            # We only attempt parsing for lines that start with
            # numeric Sl and Emp Code.
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
                            "Could not identify employee name, "
                            "daily statuses, and trailing ESSL "
                            "summary columns."
                        ),
                    )
                )
                continue

            try:
                record = _parse_row(
                    row_data,
                    metadata,
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
