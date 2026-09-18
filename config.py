from pathlib import Path

EXPECTED_REPORT_TITLE = "Monthly Status Report (Basic Report)"
SUMMARY_COLUMNS = ["P", "A", "L", "H", "HP", "WO", "WOP"]

OUTPUT_WORKBOOK_NAME = "attendance_summary.xlsx"
OUTPUT_SHEET_NAME = "Attendance Summary"

OUTPUT_COLUMNS = [
    "Employee ID",
    "Employee Name",
    "ESSL Present Days",
    "Payroll Present Days",
    "ESSL Absent Count",
    "Payroll Unpaid Absent Days",
    "Payroll Unpaid Absent Dates",
    "ESSL Absent Days",
    "Active From",
    "Active To",
    "Policy Paid Days",
    "Public Holiday Paid Days",
    "Weekend Paid Days",
    "Payroll Paid Dates",
]

LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "app.log"
