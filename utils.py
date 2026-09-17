"""
Utility functions for parsing ESSL Monthly Status Reports.

The ESSL Basic Report has a structure similar to:

    Sl Emp.Code Name <daily status cells...> P A L H HP WO WOP

Important:
    pdfplumber.extract_text() does not preserve empty table cells.
    Therefore, the number of extracted daily status tokens can vary
    from employee to employee.

This module intentionally does NOT assume that exactly `days_in_month`
daily status tokens exist.

Instead, it:
    1. Reads Sl and Emp Code from the first two tokens.
    2. Reads the final 7 numeric tokens as the machine summary.
    3. Walks backwards through the middle section and identifies
       recognized attendance status tokens.
    4. Treats all remaining middle tokens as the employee name.
"""

from __future__ import annotations

import calendar
import re
from typing import Dict, List, Optional, Any


# ESSL summary columns at the end of every employee row.
SUMMARY_COLUMNS = [
    "P",
    "A",
    "L",
    "H",
    "HP",
    "WO",
    "WOP",
]

NUM_SUMMARY_COLUMNS = len(SUMMARY_COLUMNS)


# Recognized daily attendance status values.
#
# ESSL may output:
#   P
#   A
#   L
#   H
#   HP
#   WO
#   WOP
#   ½P
#   1/2P
#   0.5P
#
# We normalize them before further processing.
DAILY_STATUS_ALIASES = {
    "P": "P",
    "A": "A",
    "L": "L",
    "H": "H",
    "HP": "HP",
    "WO": "WO",
    "WOP": "WOP",
    "½P": "½P",
    "1/2P": "½P",
    "0.5P": "½P",
    "0.5": "½P",
}


# Values used to calculate fallback attendance when the summary
# cannot be trusted or is unavailable.
DEFAULT_FALLBACK_WEIGHTS = {
    "P": 1.0,
    "½P": 0.5,
    "A": 0.0,
    "WO": 0.0,
    "WOP": 0.0,
    "L": 0.0,
    "H": 0.0,
    "HP": 0.0,
}


# Only A is considered an explicit absence.
ABSENT_STATUS = {"A"}


# Examples:
#   Aug 01 2026 To Aug 31 2026
#   Aug 01 2026 TO Aug 31 2026
#   Aug 01 2026 to Aug 31 2026
_DATE_RANGE_RE = re.compile(
    r"""
    (?P<start_month>[A-Za-z]{3,9})
    \s+
    (?P<start_day>\d{1,2})
    \s+
    (?P<start_year>\d{4})
    \s+
    To
    \s+
    (?P<end_month>[A-Za-z]{3,9})
    \s+
    (?P<end_day>\d{1,2})
    \s+
    (?P<end_year>\d{4})
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def normalize_status(token: str) -> Optional[str]:
    """
    Normalize a raw ESSL status token.

    Returns:
        Canonical status string, or None if the token is not
        recognized as a daily attendance status.
    """
    if token is None:
        return None

    value = str(token).strip()

    if not value:
        return None

    # Normalize unicode fraction / spacing variants.
    value = value.replace(" ", "")
    value = value.replace("𝟭", "1")

    # First try exact value.
    if value in DAILY_STATUS_ALIASES:
        return DAILY_STATUS_ALIASES[value]

    # Then uppercase.
    upper_value = value.upper()

    if upper_value in DAILY_STATUS_ALIASES:
        return DAILY_STATUS_ALIASES[upper_value]

    return None


def is_numeric_token(token: str) -> bool:
    """
    Return True if a token can be interpreted as a number.

    Supports:
        20
        13.5
        9.5
        0
        9999
    """
    if token is None:
        return False

    value = str(token).strip()

    if not value:
        return False

    # Remove common formatting characters.
    value = value.replace(",", "")

    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def to_number(token: str) -> float:
    """
    Convert a numeric token to float.

    Raises:
        ValueError if the token is not numeric.
    """
    value = str(token).strip().replace(",", "")
    return float(value)


def to_display_number(value: float) -> float | int:
    """
    Convert 20.0 to 20 while preserving values such as 13.5.
    """
    if float(value).is_integer():
        return int(value)
    return float(value)


def extract_date_range(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract the report date range from ESSL report text.

    Example:
        Aug 01 2026 To Aug 31 2026

    Returns:
        {
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "month": 8,
            "year": 2026,
            "days_in_month": 31,
            "date_range_text": "Aug 01 2026 To Aug 31 2026"
        }

    Returns None when the date range cannot be found.
    """
    if not text:
        return None

    match = _DATE_RANGE_RE.search(text)

    if not match:
        return None

    start_month_text = match.group("start_month").strip().lower()
    end_month_text = match.group("end_month").strip().lower()

    start_month = _MONTH_NAME_TO_NUMBER.get(start_month_text)
    end_month = _MONTH_NAME_TO_NUMBER.get(end_month_text)

    if start_month is None or end_month is None:
        return None

    start_day = int(match.group("start_day"))
    start_year = int(match.group("start_year"))

    end_day = int(match.group("end_day"))
    end_year = int(match.group("end_year"))

    try:
        import datetime

        start_date = datetime.date(
            start_year,
            start_month,
            start_day,
        )

        end_date = datetime.date(
            end_year,
            end_month,
            end_day,
        )
    except ValueError:
        return None

    if end_date < start_date:
        return None

    days_in_month = calendar.monthrange(
        end_year,
        end_month,
    )[1]

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "month": end_month,
        "year": end_year,
        "days_in_month": days_in_month,
        "date_range_text": match.group(0),
    }


def split_employee_row(
    line: str,
    days_in_month: int,
) -> Optional[Dict[str, Any]]:
    """
    Parse one ESSL employee row.

    This is the critical fix.

    OLD APPROACH:
        Expected exactly 31 daily status tokens.

    PROBLEM:
        pdfplumber removes empty PDF table cells.
        Late joiners and zero-attendance employees therefore have
        fewer extracted tokens.

    NEW APPROACH:
        First two tokens:
            Sl
            Emp Code

        Final seven numeric tokens:
            P A L H HP WO WOP

        Middle section:
            Employee name + whatever daily status tokens survived
            PDF text extraction.

        We walk backwards through the middle section and consume
        recognized attendance statuses. Everything before those
        statuses is treated as the employee name.

    Examples:

        Normal employee:
        1 3 Rohit Bhalekar P WO P ... P 20 3 0 0 0 6 0

        Late joiner:
        48 71 Paresh Jadhav A A WO P P ... P 16 3 0 0 0 5 0

        No attendance:
        1 1 1 A 0 1 0 0 0 0 0

    Returns:
        {
            "employee_id": "...",
            "employee_name": "...",
            "status_tokens": [...],
            "summary": {
                "P": ...,
                "A": ...,
                "L": ...,
                "H": ...,
                "HP": ...,
                "WO": ...,
                "WOP": ...
            },
            "raw_line": line
        }

    Returns None when the row cannot be structurally identified.
    """
    if not line:
        return None

    line = " ".join(str(line).split())

    if not line:
        return None

    tokens = line.split()

    # Need:
    #   first 2 tokens -> Sl + Emp Code
    #   last 7 tokens -> summary
    #
    # At minimum this leaves one middle token.
    if len(tokens) < (2 + NUM_SUMMARY_COLUMNS + 1):
        return None

    sl_token = tokens[0]
    employee_code_token = tokens[1]

    # Sl and employee code must be numeric.
    if not sl_token.isdigit():
        return None

    if not is_numeric_token(employee_code_token):
        return None

    # Last seven values are the ESSL summary columns.
    summary_tokens = tokens[-NUM_SUMMARY_COLUMNS:]

    if not all(is_numeric_token(token) for token in summary_tokens):
        return None

    summary: Dict[str, float | int] = {}

    for column_name, raw_value in zip(
        SUMMARY_COLUMNS,
        summary_tokens,
    ):
        summary[column_name] = to_display_number(
            to_number(raw_value)
        )

    # Everything between employee code and the summary.
    middle_tokens = tokens[2:-NUM_SUMMARY_COLUMNS]

    if not middle_tokens:
        return None

    # Walk backwards through the middle section.
    #
    # Why backwards?
    #
    # The daily statuses are always located immediately before the
    # summary in an employee row. We do not need to know how many
    # status cells were originally present.
    status_tokens_reversed: List[str] = []

    index = len(middle_tokens) - 1

    while index >= 0:
        normalized = normalize_status(
            middle_tokens[index]
        )

        if normalized is None:
            break

        status_tokens_reversed.append(normalized)
        index -= 1

    status_tokens = list(reversed(status_tokens_reversed))

    # Remaining prefix is the employee name.
    name_tokens = middle_tokens[: index + 1]

    employee_name = " ".join(name_tokens).strip()

    if not employee_name:
        return None

    return {
        "sl": sl_token,
        "employee_id": employee_code_token,
        "employee_name": employee_name,
        "status_tokens": status_tokens,
        "summary": summary,
        "raw_line": line,
        "daily_status_count": len(status_tokens),
        "daily_status_complete": len(status_tokens) == days_in_month,
    }


def calculate_fallback_present(
    status_tokens: List[str],
    fallback_weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Calculate days present from daily status tokens.

    This should be used only when the machine summary is unavailable
    or considered invalid.

    Default weights:
        P   = 1
        ½P  = 0.5
        A   = 0
        WO  = 0
        WOP = 0
        L   = 0
        H   = 0
        HP  = 0
    """
    if fallback_weights is None:
        fallback_weights = DEFAULT_FALLBACK_WEIGHTS

    total = 0.0

    for raw_status in status_tokens:
        status = normalize_status(raw_status)

        if status is None:
            continue

        total += float(
            fallback_weights.get(status, 0.0)
        )

    return total


def calculate_absent_days(
    status_tokens: List[str],
    *,
    days_in_month: Optional[int] = None,
    complete_only: bool = True,
) -> List[int]:
    """
    Return 1 based absent day numbers.

    IMPORTANT:
        pdfplumber can remove blank PDF cells.

    Therefore, when the daily status sequence is shorter than the
    actual month, its positions cannot safely be mapped back to
    calendar dates.

    By default:
        complete_only=True

    means absent day numbers are returned only when all daily
    status cells were successfully extracted.

    For incomplete rows:
        []

    is returned rather than returning incorrect calendar dates.

    This prevents a late joiner's absence sequence from being
    incorrectly shifted to day 1, day 2, day 3, etc.
    """
    if not status_tokens:
        return []

    if (
        complete_only
        and days_in_month is not None
        and len(status_tokens) != days_in_month
    ):
        return []

    absent_days: List[int] = []

    for day_index, raw_status in enumerate(
        status_tokens,
        start=1,
    ):
        status = normalize_status(raw_status)

        if status in ABSENT_STATUS:
            absent_days.append(day_index)

    return absent_days


def summarize_status_tokens(
    status_tokens: List[str],
) -> Dict[str, float]:
    """
    Produce a summary from extracted daily statuses.

    This is primarily useful for validation/debugging.
    """
    result = {
        column: 0.0
        for column in SUMMARY_COLUMNS
    }

    for raw_status in status_tokens:
        status = normalize_status(raw_status)

        if status in result:
            result[status] += 1.0

    return result


def is_complete_daily_sequence(
    status_tokens: List[str],
    days_in_month: int,
) -> bool:
    """
    Return True when the expected number of daily cells survived
    text extraction.
    """
    return len(status_tokens) == days_in_month


def calculate_daily_present_from_tokens(
    status_tokens: List[str],
    fallback_weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Convenience wrapper for fallback present calculation.
    """
    return calculate_fallback_present(
        status_tokens=status_tokens,
        fallback_weights=fallback_weights,
    )
