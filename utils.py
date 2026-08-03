"""
utils.py

Small, dependency-light helper functions shared by parser.py and app.py.
Nothing here should know about Streamlit or openpyxl — keep it pure.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from typing import Optional

from dateutil import parser as dateparser

from config import ABSENT_STATUS, FALLBACK_STATUS_WEIGHTS, NUM_SUMMARY_COLUMNS

# ---------------------------------------------------------------------------
# Number parsing — the report sometimes prints half-day totals as "16.5",
# "8.5", etc. Plain float() handles that; this wrapper just guards against
# stray characters and keeps a single call site.
# ---------------------------------------------------------------------------
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def is_numeric_token(token: str) -> bool:
    return bool(_NUMERIC_RE.match(token.strip()))


def to_number(token: str) -> float:
    token = token.strip()
    if not is_numeric_token(token):
        raise ValueError(f"Not a numeric token: {token!r}")
    return float(token)


def format_days_present(value: float) -> float | int:
    """Return an int when the value has no fractional part, else the float."""
    if float(value).is_integer():
        return int(value)
    return value


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------
_DATE_RANGE_RE = re.compile(
    r"([A-Za-z]{3}\s+\d{1,2}\s+\d{4})\s+To\s+([A-Za-z]{3}\s+\d{1,2}\s+\d{4})",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(r"Company:\s*(.+?)\s+Printed On", re.IGNORECASE)
_DEPARTMENT_RE = re.compile(r"Department\s*[:]?\s*(.+)", re.IGNORECASE)


@dataclass
class ReportMetadata:
    report_name: Optional[str] = None
    month: Optional[str] = None
    year: Optional[int] = None
    days_in_month: Optional[int] = None
    company: Optional[str] = None
    department: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def extract_metadata(full_text: str, expected_title: str) -> ReportMetadata:
    meta = ReportMetadata()

    if expected_title.lower() in full_text.lower():
        meta.report_name = expected_title
    else:
        # Grab whatever the first non-empty line says instead of failing hard.
        first_line = next((l for l in full_text.splitlines() if l.strip()), None)
        meta.report_name = first_line
        meta.warnings.append(
            f"Report title did not match expected '{expected_title}'."
        )

    date_match = _DATE_RANGE_RE.search(full_text)
    if date_match:
        start_str, end_str = date_match.group(1), date_match.group(2)
        meta.period_start, meta.period_end = start_str, end_str
        try:
            start_dt = dateparser.parse(start_str)
            end_dt = dateparser.parse(end_str)
            meta.month = start_dt.strftime("%B")
            meta.year = start_dt.year
            meta.days_in_month = (end_dt - start_dt).days + 1
        except (ValueError, OverflowError) as exc:
            meta.warnings.append(f"Could not parse date range: {exc}")
    else:
        meta.warnings.append("Date range (e.g. 'Jul 01 2026 To Jul 31 2026') not found.")

    # Fallback for days_in_month if the date range parse failed but we do
    # have a month/year some other way — not expected in practice, but keep
    # the report robust rather than crashing.
    if meta.days_in_month is None and meta.month and meta.year:
        month_num = list(calendar.month_name).index(meta.month) if meta.month in calendar.month_name else None
        if month_num:
            meta.days_in_month = calendar.monthrange(meta.year, month_num)[1]

    company_match = _COMPANY_RE.search(full_text)
    if company_match:
        meta.company = company_match.group(1).strip()

    dept_match = _DEPARTMENT_RE.search(full_text)
    if dept_match:
        meta.department = dept_match.group(1).strip()

    return meta


# ---------------------------------------------------------------------------
# Employee row tokenizing
# ---------------------------------------------------------------------------
def split_employee_row(line: str, days_in_month: int) -> Optional[dict]:
    """
    Attempt to split one text line from the report into its component
    parts: Sl No, Employee Code, Employee Name, daily status list, and the
    trailing summary numbers (P A L H HP WO WOP).

    Returns None if the line doesn't look like an employee data row at all
    (e.g. it's a header/footer/title line).

    This never assumes fixed column positions — it works from both ends of
    the token list inward, so it tolerates the header text reflowing or the
    exact spacing changing between exports.
    """
    tokens = line.split()

    min_len = 2 + 1 + days_in_month + NUM_SUMMARY_COLUMNS  # Sl + Code + >=1 name word + statuses + summary
    if len(tokens) < min_len:
        return None

    sl_token, code_token = tokens[0], tokens[1]
    if not (sl_token.isdigit() and code_token.isdigit()):
        # Header rows ("Sl Emp. Code Name ...") and continuation header rows
        # ("St M W Th ...") don't start with two integers.
        return None

    tail = tokens[-NUM_SUMMARY_COLUMNS:]
    if not all(is_numeric_token(t) for t in tail):
        return None

    status_tokens = tokens[-(NUM_SUMMARY_COLUMNS + days_in_month): -NUM_SUMMARY_COLUMNS]
    name_tokens = tokens[2: -(NUM_SUMMARY_COLUMNS + days_in_month)]

    if not name_tokens or len(status_tokens) != days_in_month:
        return None

    return {
        "sl": sl_token,
        "employee_code": code_token,
        "employee_name": " ".join(name_tokens),
        "status_tokens": status_tokens,
        "summary_tokens": tail,
    }


# ---------------------------------------------------------------------------
# Attendance calculation from daily status cells
# ---------------------------------------------------------------------------
def calculate_fallback_present(status_tokens: list[str]) -> tuple[float, list[str]]:
    """Return (days_present, unmapped_tokens) using FALLBACK_STATUS_WEIGHTS."""
    total = 0.0
    unmapped = []
    for tok in status_tokens:
        if tok in FALLBACK_STATUS_WEIGHTS:
            total += FALLBACK_STATUS_WEIGHTS[tok]
        else:
            unmapped.append(tok)
    return total, unmapped


def calculate_absent_days(status_tokens: list[str]) -> tuple[int, list[int]]:
    """Return (absent_count, [1-indexed day numbers]) based on 'A' statuses."""
    days = [i + 1 for i, tok in enumerate(status_tokens) if tok in ABSENT_STATUS]
    return len(days), days
