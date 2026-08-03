"""
excel_writer.py

Builds the formatted attendance_summary.xlsx workbook and returns it as a
BytesIO buffer, ready for st.download_button or writing straight to disk.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from config import OUTPUT_COLUMNS, OUTPUT_SHEET_NAME
from parser import EmployeeRecord


def _autofit_columns(ws: Worksheet) -> None:
    for col_idx, column_cells in enumerate(ws.columns, start=1):
        max_len = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=8)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 60)


def build_workbook(records: list[EmployeeRecord]) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = OUTPUT_SHEET_NAME

    ws.append(OUTPUT_COLUMNS)
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font

    center = Alignment(horizontal="center")
    left = Alignment(horizontal="left")

    for record in records:
        row = record.to_output_row()
        ws.append([row[col] for col in OUTPUT_COLUMNS])

    for row_idx in range(2, ws.max_row + 1):
        ws.cell(row=row_idx, column=1).alignment = center  # Employee ID
        ws.cell(row=row_idx, column=2).alignment = left     # Employee Name
        ws.cell(row=row_idx, column=3).alignment = center  # Days Present
        ws.cell(row=row_idx, column=4).alignment = center  # Absent Count
        ws.cell(row=row_idx, column=5).alignment = left     # Absent Days

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(OUTPUT_COLUMNS))}{ws.max_row}"
    _autofit_columns(ws)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
