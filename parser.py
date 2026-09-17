"""
parser.py

ESSL Monthly Status Report parser.

The ESSL Basic Report has this logical structure:

    Sl Emp.Code Name <daily status cells...> P A L H HP WO WOP

Important:
    pdfplumber does not reliably preserve empty PDF table cells.

Therefore:
    We do NOT require exactly 31 daily status tokens.

Instead:
    1. First token  = Sl
    2. Second token = Employee Code
    3. Last 7 numeric tokens = P A L H HP WO WOP
    4. Everything between them is parsed as employee name + daily statuses

Primary attendance source:
    ESSL machine generated P summary.

Fallback attendance source:
    Daily attendance statuses, only when the machine summary is invalid.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


# ---------------------------------------------------------------------------
# REPORT METADATA
# ---------------------------------------------------------------------------

@dataclass
class ReportMetadata:
    """
    Metadata extracted from the ESSL report.

    The additional fields are kept for compatibility with app.py.
    """

    start_date: Optional[str] = None
    end_date: Optional[str] = None

    month: Optional[int] = None
    year: Optional[int] = None

    days_in_month: int = 31

    date_range_text: Optional[str] = None
    pdf_name: Optional[str] = None

    # Fields expected by the existing Streamlit UI.
    company: Optional[str] = None
    department: Optional[str] = None
    report_name: Optional[str] = None

    warnings: List[str] = field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# FAILED ROW
# ---------------------------------------------------------------------------

@dataclass
class FailedRow:
    """
    Information about a row that could not be parsed.

    Supports both:
        row.raw_line
        row["raw_line"]

    because older app.py code uses dictionary style access.
    """

    line_number: int
    raw_line: str
    reason: str

    @property
    def error(self) -> str:
        """
        Compatibility alias because app.py expects row['error'].
        """
        return self.reason

    def __getitem__(self, key: str) -> Any:
        """
        Allow dictionary style access.
        """
        if key == "line_number":
            return self.line_number

        if key == "raw_line":
            return self.raw_line

        if key == "reason":
            return self.reason

        if key == "error":
            return self.reason

        raise KeyError(key)


# ---------------------------------------------------------------------------
# EMPLOYEE RECORD
# ---------------------------------------------------------------------------

@dataclass
class EmployeeRecord:
    """
    Normalized employee attendance record.

    Kept compatible with excel_writer.py.
    """

    employee_id: str
    employee_name: str

    days_present: float
    absent_count: float

    absent_days: List[int] = field(
        default_factory=list
    )

    source: str = "summary"

    warnings: List[str] = field(
        default_factory=list
    )

    summary: Dict[str, Any] = field(
        default_factory=dict
    )

    status_tokens: List[str] = field(
        default_factory=list
    )

    daily_status_count: int = 0

    daily_status_complete: bool = False

    raw_line: str = ""

    def to_output_row(self) -> Dict[str, Any]:
        """
        Convert the record into the structure expected by
        excel_writer.py.
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


# ---------------------------------------------------------------------------
# PARSE RESULT
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """
    Complete parsing result.

    The attributes primary_count and fallback_count are intentionally
    provided because app.py and logger.py expect these exact names.
    """

    records: List[EmployeeRecord]

    failed_rows: List[FailedRow]

    metadata: ReportMetadata

    employees_found: int = 0

    primary_count: int = 0

    fallback_count: int = 0

    # Compatibility alias for code that may use fallback_used.
    @property
    def fallback_used(self) -> int:
        return self.fallback_count

    @property
    def successfully_parsed(self) -> int:
        return len(self.records)

    @property
    def failed_count(self) -> int:
        return len(self.failed_rows)


# ---------------------------------------------------------------------------
# NUMBER HELPERS
# ---------------------------------------------------------------------------

def _normalize_number(
    value: Any,
) -> float | int:
    """
    Convert:
        20.0 -> 20
        13.5 -> 13.5
    """

    numeric = float(value)

    if numeric.is_integer():
        return int(numeric)

    return numeric


# ---------------------------------------------------------------------------
# EMPLOYEE ROW DETECTION
# ---------------------------------------------------------------------------

def _looks_like_employee_row(
    line: str,
) -> bool:
    """
    Determine whether a line begins like an ESSL employee row.

    Expected:

        Sl Emp.Code ...
    """

    if not line:
        return False

    tokens = line.split()

    if len(tokens) < 2:
        return False

    first_token = tokens[0]
    second_token = tokens[1]

    if not first_token.isdigit():
        return False

    try:
        float(second_token)
        return True
    except (
        TypeError,
        ValueError,
    ):
        return False


# ---------------------------------------------------------------------------
# SUMMARY VALIDATION
# ---------------------------------------------------------------------------

def _is_summary_valid(
    summary: Dict[str, Any],
    days_in_month: int,
) -> bool:
    """
    Validate the final ESSL summary:

        P A L H HP WO WOP
    """

    for column in SUMMARY_COLUMNS:

        if column not in summary:
            return False

        try:
            value = float(
                summary[column]
            )
        except (
            TypeError,
            ValueError,
        ):
            return False

        if value < 0:
            return False

    try:
        present = float(
            summary["P"]
        )

        absent = float(
            summary["A"]
        )

    except (
        TypeError,
        ValueError,
    ):
        return False

    if present > days_in_month:
        return False

    if absent > days_in_month:
        return False

    return True


# ---------------------------------------------------------------------------
# DAILY VS MACHINE SUMMARY CHECK
# ---------------------------------------------------------------------------

def _daily_summary_matches_machine_summary(
    status_tokens: List[str],
    summary: Dict[str, Any],
) -> bool:
    """
    Compare derived daily present count against ESSL P.

    This is called only when the complete daily sequence exists.
    """

    if not status_tokens:
        return False

    derived_present = (
        calculate_fallback_present(
            status_tokens,
            FALLBACK_STATUS_WEIGHTS,
        )
    )

    machine_present = float(
        summary["P"]
    )

    return abs(
        derived_present - machine_present
    ) < 0.01


# ---------------------------------------------------------------------------
# ROW -> EMPLOYEE RECORD
# ---------------------------------------------------------------------------

def _parse_row(
    row_data: Dict[str, Any],
    metadata: ReportMetadata,
) -> EmployeeRecord:
    """
    Convert parsed row data into EmployeeRecord.
    """

    employee_id = str(
        row_data["employee_id"]
    ).strip()

    employee_name = str(
        row_data["employee_name"]
    ).strip()

    # Normalize daily statuses.
    status_tokens: List[str] = []

    for raw_status in row_data.get(
        "status_tokens",
        [],
    ):

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

    # =======================================================================
    # PRIMARY SOURCE
    # =======================================================================

    if _is_summary_valid(
        summary,
        metadata.days_in_month,
    ):

        # ESSL's P column is authoritative.
        days_present = _normalize_number(
            summary["P"]
        )

        # ESSL's A column is authoritative.
        absent_count = _normalize_number(
            summary["A"]
        )

        source = "summary"

        # Exact absence dates can only be calculated safely if every
        # daily cell was preserved during PDF extraction.
        absent_days = calculate_absent_days(
            status_tokens,
            days_in_month=metadata.days_in_month,
            complete_only=True,
        )

        if not daily_status_complete:

            warnings.append(
                "Daily attendance cells were incomplete after PDF "
                "text extraction. ESSL summary P/A values were "
                "used as authoritative totals."
            )

            warnings.append(
                "Exact absence dates were not derived because "
                "the extracted daily sequence was incomplete."
            )

        else:

            # Full sequence available, so we can cross check P.
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

    # =======================================================================
    # FALLBACK SOURCE
    # =======================================================================

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
            "was calculated from daily attendance statuses."
        )

        if not daily_status_complete:

            warnings.append(
                "The daily attendance sequence was incomplete "
                "after PDF text extraction."
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


# ---------------------------------------------------------------------------
# METADATA EXTRACTION
# ---------------------------------------------------------------------------

def _extract_metadata(
    full_text: str,
    pdf_name: Optional[str],
) -> ReportMetadata:
    """
    Extract metadata from report text.
    """

    metadata = ReportMetadata(
        pdf_name=pdf_name,
        days_in_month=31,
        report_name="Monthly Status Report (Basic Report)",
    )

    # ---------------------------------------------------------
    # Date range
    # ---------------------------------------------------------

    date_info = extract_date_range(
        full_text
    )

    if date_info:

        metadata.start_date = (
            date_info.get(
                "start_date"
            )
        )

        metadata.end_date = (
            date_info.get(
                "end_date"
            )
        )

        metadata.month = (
            date_info.get(
                "month"
            )
        )

        metadata.year = (
            date_info.get(
                "year"
            )
        )

        metadata.days_in_month = int(
            date_info.get(
                "days_in_month",
                31,
            )
        )

        metadata.date_range_text = (
            date_info.get(
                "date_range_text"
            )
        )

    # ---------------------------------------------------------
    # Company
    # ---------------------------------------------------------

    for line in full_text.splitlines():

        cleaned = " ".join(
            line.split()
        ).strip()

        lower = cleaned.lower()

        if lower.startswith(
            "company:"
        ):

            company_value = cleaned[
                len("Company:"):
            ].strip()

            # The ESSL PDF can produce:
            #
            # Company: AI Printed On : Sep 17 2026 13:09
            #
            # Remove everything after Printed On.
            if "Printed On" in company_value:
                company_value = (
                    company_value.split(
                        "Printed On",
                        1,
                    )[0]
                ).strip()

            metadata.company = (
                company_value
                or None
            )

            break

    # ---------------------------------------------------------
    # Department
    # ---------------------------------------------------------

    for line in full_text.splitlines():

        cleaned = " ".join(
            line.split()
        ).strip()

        if cleaned.lower().startswith(
            "department "
        ):

            department_value = (
                cleaned[
                    len("Department ")
                :].strip()
            )

            metadata.department = (
                department_value
                or None
            )

            break

        if cleaned.lower() == "department default":
            metadata.department = "Default"
            break

    # ---------------------------------------------------------
    # Metadata warning if date was not found
    # ---------------------------------------------------------

    if not metadata.start_date:
        metadata.warnings.append(
            "Report date range could not be extracted."
        )

    if metadata.month is None:
        metadata.warnings.append(
            "Report month could not be extracted."
        )

    if metadata.year is None:
        metadata.warnings.append(
            "Report year could not be extracted."
        )

    return metadata


# ---------------------------------------------------------------------------
# MAIN PARSER
# ---------------------------------------------------------------------------

def parse_report(
    pdf_bytes: bytes,
    pdf_name: Optional[str] = None,
) -> ParseResult:
    """
    Parse an ESSL Monthly Status Report PDF.

    The parser scans all PDF pages.

    Employee detection is independent of the number of daily attendance
    cells successfully extracted by pdfplumber.
    """

    if not pdf_bytes:
        raise ValueError(
            "PDF input is empty."
        )

    records: List[
        EmployeeRecord
    ] = []

    failed_rows: List[
        FailedRow
    ] = []

    page_texts: List[str] = []

    # -----------------------------------------------------------------------
    # EXTRACT PDF TEXT
    # -----------------------------------------------------------------------

    try:

        with pdfplumber.open(
            io.BytesIO(pdf_bytes)
        ) as pdf:

            for page_number, page in enumerate(
                pdf.pages,
                start=1,
            ):

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

    # -----------------------------------------------------------------------
    # METADATA
    # -----------------------------------------------------------------------

    full_text = "\n".join(
        page_texts
    )

    metadata = _extract_metadata(
        full_text=full_text,
        pdf_name=pdf_name,
    )

    # -----------------------------------------------------------------------
    # PARSE EMPLOYEE ROWS
    # -----------------------------------------------------------------------

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

            # ----------------------------------------------------------------
            # Ignore report headers / footers.
            # ----------------------------------------------------------------

            if (
                "monthly status report" in lower_line
                or "department default" in lower_line
                or "sl emp. code" in lower_line
                or "generated by:essl" in lower_line
                or "generated by: essl" in lower_line
                or lower_line.startswith(
                    "company:"
                )
                or "page no." in lower_line
            ):
                continue

            # ----------------------------------------------------------------
            # Identify employee rows.
            # ----------------------------------------------------------------

            if not _looks_like_employee_row(
                line
            ):
                continue

            # ----------------------------------------------------------------
            # Parse employee row structure.
            # ----------------------------------------------------------------

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

            # ----------------------------------------------------------------
            # Convert to EmployeeRecord.
            # ----------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # COUNTS EXPECTED BY app.py AND logger.py
    # -----------------------------------------------------------------------

    primary_count = sum(
        1
        for record in records
        if record.source == "summary"
    )

    fallback_count = sum(
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
        primary_count=primary_count,
        fallback_count=fallback_count,
    )
