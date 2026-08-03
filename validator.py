"""
validator.py

Post-parse validation and cleanup:
- Employee ID / Name presence
- Attendance within [0, days_in_month]
- Duplicate Employee IDs -> keep the most complete record, log the rest
"""

from __future__ import annotations

from dataclasses import dataclass, field

from logger import logger
from parser import EmployeeRecord


@dataclass
class ValidationResult:
    valid_records: list[EmployeeRecord]
    invalid_records: list[tuple[EmployeeRecord, str]] = field(default_factory=list)
    duplicates_dropped: list[tuple[str, str]] = field(default_factory=list)  # (emp_id, reason)


def _completeness_score(record: EmployeeRecord) -> tuple:
    """Higher is 'more complete' — fewer warnings, has a non-numeric name, etc."""
    has_real_name = not record.employee_name.strip().isdigit()
    return (has_real_name, -len(record.warnings), record.days_present)


def validate_records(records: list[EmployeeRecord], days_in_month: int) -> ValidationResult:
    invalid: list[tuple[EmployeeRecord, str]] = []
    by_id: dict[str, EmployeeRecord] = {}
    duplicates_dropped: list[tuple[str, str]] = []

    for record in records:
        reason = None
        if not record.employee_id or not record.employee_id.strip():
            reason = "Missing Employee ID"
        elif not record.employee_name or not record.employee_name.strip():
            reason = "Missing Employee Name"
        elif record.days_present < 0:
            reason = f"Negative attendance ({record.days_present})"
        elif record.days_present > days_in_month:
            reason = (
                f"Attendance ({record.days_present}) exceeds days in month "
                f"({days_in_month})"
            )

        if reason:
            invalid.append((record, reason))
            logger.warning(f"Invalid record dropped: emp {record.employee_id} — {reason}")
            continue

        existing = by_id.get(record.employee_id)
        if existing is None:
            by_id[record.employee_id] = record
        else:
            # Duplicate Employee ID — keep whichever record is more complete.
            keep, drop = (
                (existing, record)
                if _completeness_score(existing) >= _completeness_score(record)
                else (record, existing)
            )
            by_id[record.employee_id] = keep
            duplicates_dropped.append(
                (record.employee_id, f"Duplicate Employee ID; kept the more complete record")
            )
            logger.warning(
                f"Duplicate Employee ID {record.employee_id} — kept most complete record."
            )

    return ValidationResult(
        valid_records=list(by_id.values()),
        invalid_records=invalid,
        duplicates_dropped=duplicates_dropped,
    )
