"""
app.py

Streamlit front-end for the Employee Attendance Extractor.

Workflow: Upload PDF -> Extract metadata -> Extract employees -> Extract
attendance -> Extract absent dates -> Validate -> Preview -> Generate Excel
-> Download.
"""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from config import OUTPUT_WORKBOOK_NAME
from excel_writer import build_workbook
from logger import log_run_summary, logger
from parser import ParseResult, parse_report
from validator import validate_records

st.set_page_config(page_title="Employee Attendance Extractor", layout="wide")

st.title("📋 Employee Attendance Extractor")
st.caption(
    "Converts an ESSL Monthly Status Report (Basic Report) PDF into a clean "
    "Excel attendance summary. Works for any month — no code changes needed."
)

with st.sidebar:
    st.header("1. Upload")
    uploaded_file = st.file_uploader("Monthly Status Report (Basic Report) PDF", type=["pdf"])
    process_clicked = st.button("Process Report", type="primary", disabled=uploaded_file is None)
    st.divider()
    download_placeholder = st.container()


def _render_metadata(result: ParseResult) -> None:
    meta = result.metadata
    cols = st.columns(4)
    cols[0].metric("Month", meta.month or "—")
    cols[1].metric("Year", meta.year or "—")
    cols[2].metric("Days in Period", meta.days_in_month or "—")
    cols[3].metric("Department", meta.department or "—")
    st.caption(f"Company: {meta.company or 'Unknown'} · Report: {meta.report_name or 'Unknown'}")
    if meta.warnings:
        with st.expander("Metadata warnings"):
            for w in meta.warnings:
                st.write(f"- {w}")


def _render_stats(result: ParseResult, validation) -> None:
    cols = st.columns(4)
    cols[0].metric("Employees Found", result.employees_found)
    cols[1].metric("Successfully Parsed", len(result.records))
    cols[2].metric("Fallback Used", result.fallback_count)
    cols[3].metric("Failed Records", len(result.failed_rows) + len(validation.invalid_records))


if uploaded_file is None:
    st.info("Upload a Monthly Status Report (Basic Report) PDF from the sidebar to begin.")
    st.stop()

if not process_clicked and "last_result" not in st.session_state:
    st.info("Click **Process Report** in the sidebar when you're ready.")
    st.stop()

if process_clicked:
    pdf_bytes = uploaded_file.getvalue()
    start = time.perf_counter()
    try:
        with st.spinner("Reading and parsing report..."):
            result = parse_report(pdf_bytes, pdf_name=uploaded_file.name)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Unable to read report: {uploaded_file.name}")
        st.error("Unable to read report.")
        st.exception(exc)
        st.stop()

    if not result.records and not result.failed_rows:
        st.error("No attendance records detected.")
        st.stop()

    validation = validate_records(result.records, result.metadata.days_in_month or 31)
    elapsed = time.perf_counter() - start

    log_run_summary(
        pdf_name=uploaded_file.name,
        processing_time_s=elapsed,
        employees_parsed=len(validation.valid_records),
        primary_count=result.primary_count,
        fallback_count=result.fallback_count,
        failed_rows=len(result.failed_rows) + len(validation.invalid_records),
        duplicate_rows=len(validation.duplicates_dropped),
    )

    st.session_state["last_result"] = result
    st.session_state["last_validation"] = validation
    st.session_state["last_elapsed"] = elapsed
    st.session_state["last_filename"] = uploaded_file.name

result: ParseResult = st.session_state["last_result"]
validation = st.session_state["last_validation"]

st.success(f"Processed in {st.session_state['last_elapsed']:.2f}s")

st.subheader("Report Overview")
_render_metadata(result)

st.subheader("Processing Summary")
_render_stats(result, validation)

if validation.duplicates_dropped:
    with st.expander(f"⚠️ {len(validation.duplicates_dropped)} duplicate Employee ID(s) resolved"):
        for emp_id, reason in validation.duplicates_dropped:
            st.write(f"- Emp {emp_id}: {reason}")

if result.failed_rows or validation.invalid_records:
    with st.expander(f"⚠️ {len(result.failed_rows) + len(validation.invalid_records)} row(s) skipped"):
        for row in result.failed_rows:
            st.write(f"- Parse error: {row['error']} — `{row['raw_line'][:80]}...`")
        for record, reason in validation.invalid_records:
            st.write(f"- Emp {record.employee_id} ({record.employee_name}): {reason}")

st.subheader("Preview")
rows = [r.to_output_row() for r in validation.valid_records]
df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True)

excel_buffer = build_workbook(validation.valid_records)
with download_placeholder:
    st.header("2. Download")
    st.download_button(
        "⬇️ Download Excel",
        data=excel_buffer,
        file_name=OUTPUT_WORKBOOK_NAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
