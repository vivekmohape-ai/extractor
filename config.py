"""
config.py

Central configuration for the Employee Attendance Extractor.
No employee/report data lives here — only constants that describe the
ESSL "Monthly Status Report (Basic Report)" layout, so future format
tweaks (new status codes, etc.) only need to change this file.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Report identification
# ---------------------------------------------------------------------------
EXPECTED_REPORT_TITLE = "Monthly Status Report (Basic Report)"

# ---------------------------------------------------------------------------
# Summary columns, in the exact order they appear at the end of every
# employee row: P  A  L  H  HP  WO  WOP
# ---------------------------------------------------------------------------
SUMMARY_COLUMNS = ["P", "A", "L", "H", "HP", "WO", "WOP"]
NUM_SUMMARY_COLUMNS = len(SUMMARY_COLUMNS)

# ---------------------------------------------------------------------------
# Daily status → attendance-weight mapping, used ONLY as a fallback when the
# machine-generated summary (P column) cannot be extracted for a row.
# ---------------------------------------------------------------------------
FALLBACK_STATUS_WEIGHTS = {
    "P": 1.0,
    "½P": 0.5,
    "1/2P": 0.5,   # in case the ½ glyph doesn't survive extraction
    "A": 0.0,
    "WO": 0.0,
    "WOP": 0.0,
    "L": 0.0,
    "HP": 0.0,
}

# Statuses that count as an absence for the Absent Days / Absent Count logic.
ABSENT_STATUS = {"A"}

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
OUTPUT_WORKBOOK_NAME = "attendance_summary.xlsx"
OUTPUT_SHEET_NAME = "Attendance Summary"
OUTPUT_COLUMNS = [
    "Employee ID",
    "Employee Name",
    "Days Present",
    "Absent Count",
    "Absent Days",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "app.log"

# ---------------------------------------------------------------------------
# Performance targets (informational — used in tests / README, not enforced)
# ---------------------------------------------------------------------------
MAX_PROCESSING_SECONDS = 5
MAX_MEMORY_MB = 300
