from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_HOLIDAY_FILE = Path(__file__).with_name("holidays.json")


def load_holidays(path: Path = DEFAULT_HOLIDAY_FILE) -> dict[int, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    result: dict[int, dict[str, str]] = {}
    for year_key, entries in raw.items():
        year = int(year_key)
        result[year] = {
            str(item["date"]): str(item.get("name", "Public Holiday"))
            for item in entries
            if item.get("date")
        }
    return result


def is_sunday(d: date) -> bool:
    return d.weekday() == 6


def saturday_ordinal(d: date) -> int:
    if d.weekday() != 5:
        return 0
    return ((d.day - 1) // 7) + 1


def is_second_saturday(d: date) -> bool:
    return d.weekday() == 5 and saturday_ordinal(d) == 2


def is_fourth_saturday(d: date) -> bool:
    return d.weekday() == 5 and saturday_ordinal(d) == 4


def is_public_holiday(d: date, holidays: Optional[dict[int, dict[str, str]]] = None) -> bool:
    holidays = holidays if holidays is not None else load_holidays()
    return d.isoformat() in holidays.get(d.year, {})


def public_holiday_name(d: date, holidays: Optional[dict[int, dict[str, str]]] = None) -> str:
    holidays = holidays if holidays is not None else load_holidays()
    return holidays.get(d.year, {}).get(d.isoformat(), "")


def is_policy_paid_day(d: date, holidays: Optional[dict[int, dict[str, str]]] = None) -> bool:
    return (
        is_sunday(d)
        or is_second_saturday(d)
        or is_fourth_saturday(d)
        or is_public_holiday(d, holidays)
    )


STATUS_WEIGHTS = {
    "P": 1.0,
    "½P": 0.5,
    "WOP": 1.0,
    "A": 0.0,
    "WO": 0.0,
    "L": 0.0,
    "H": 0.0,
    "HP": 0.0,
}


def normalize_status(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper().replace(" ", "")
    aliases = {
        "½P": "½P",
        "1/2P": "½P",
        "0.5P": "½P",
        "P": "P",
        "PRESENT": "P",
        "A": "A",
        "WO": "WO",
        "WOP": "WOP",
        "L": "L",
        "H": "H",
        "HP": "HP",
    }
    return aliases.get(text, text)


def calculate_payroll_attendance(
    *,
    year: int,
    month: int,
    daily_statuses: list[str],
    active_start_day: Optional[int] = None,
    active_end_day: Optional[int] = None,
    holidays: Optional[dict[int, dict[str, str]]] = None,
) -> dict[str, Any]:
    holidays = holidays if holidays is not None else load_holidays()
    days_in_month = calendar.monthrange(year, month)[1]

    if active_start_day is None:
        nonblank = [idx + 1 for idx, status in enumerate(daily_statuses[:days_in_month]) if normalize_status(status)]
        active_start_day = min(nonblank) if nonblank else None

    if active_end_day is None:
        nonblank = [idx + 1 for idx, status in enumerate(daily_statuses[:days_in_month]) if normalize_status(status)]
        active_end_day = max(nonblank) if nonblank else None

    payroll_present = 0.0
    paid_policy_days = 0
    paid_holiday_days = 0
    paid_weekend_days = 0
    paid_dates: list[str] = []
    unpaid_absent_dates: list[str] = []
    daily_result: list[dict[str, Any]] = []

    for day in range(1, days_in_month + 1):
        status = normalize_status(daily_statuses[day - 1] if day - 1 < len(daily_statuses) else "")
        d = date(year, month, day)

        active = (
            active_start_day is not None
            and active_end_day is not None
            and active_start_day <= day <= active_end_day
        )

        if not active:
            value = 0.0
            reason = "outside active period"
        elif is_policy_paid_day(d, holidays):
            value = 1.0
            paid_policy_days += 1
            if is_public_holiday(d, holidays):
                paid_holiday_days += 1
                reason = f"paid public holiday: {public_holiday_name(d, holidays)}"
            else:
                paid_weekend_days += 1
                reason = "paid Sunday / 2nd Saturday / 4th Saturday"
            paid_dates.append(d.isoformat())
        else:
            value = STATUS_WEIGHTS.get(status, 0.0)
            reason = {
                "P": "ESSL Present",
                "½P": "ESSL Half Present",
                "WOP": "ESSL Weekly Off Present",
                "A": "ESSL Absent",
                "WO": "ESSL Weekly Off",
                "L": "ESSL Leave",
                "H": "ESSL Holiday/other",
                "HP": "ESSL Half Paid/other",
                "": "blank",
            }.get(status, f"ESSL status: {status or 'blank'}")
            if value > 0:
                paid_dates.append(d.isoformat())
            if status == "A":
                unpaid_absent_dates.append(d.isoformat())

        payroll_present += value
        daily_result.append(
            {
                "day": day,
                "date": d.isoformat(),
                "status": status,
                "payroll_present": value,
                "is_policy_paid_day": is_policy_paid_day(d, holidays),
                "is_public_holiday": is_public_holiday(d, holidays),
                "reason": reason,
            }
        )

    return {
        "payroll_present_days": int(payroll_present) if payroll_present.is_integer() else payroll_present,
        "paid_policy_days": paid_policy_days,
        "paid_holiday_days": paid_holiday_days,
        "paid_weekend_days": paid_weekend_days,
        "paid_dates": paid_dates,
        "unpaid_absent_dates": unpaid_absent_dates,
        "active_start_day": active_start_day,
        "active_end_day": active_end_day,
        "daily_result": daily_result,
    }
