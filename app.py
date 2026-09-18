from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from config import OUTPUT_WORKBOOK_NAME
from excel_writer import build_workbook
from logger import log_run_summary, logger
from parser import ParseResult, parse_report
from attendance_policy import load_holidays
from validator import validate_records

st.set_page_config(page_title="ESSL Attendance Extractor", layout="wide")

st.title("ESSL Attendance Extractor")
st.caption(
    "ESSL Basic Report PDF → attendance Excel with payroll present days calculated "
    "using paid Sundays, 2nd/4th Saturdays and public holidays."
)

with st.sidebar:
    uploaded_file = st.file_uploader(
        "Monthly Status Report (Basic Report) PDF",
        type=["pdf"],
    )
    process = st.button("Process Report", type="primary", disabled=uploaded_file is None)

if uploaded_file is None:
    st.info("Upload the ESSL Basic Report PDF.")
    st.stop()

if process:
    start = time.perf_counter()
    try:
        result = parse_report(uploaded_file.getvalue(), uploaded_file.name)
    except Exception as exc:
        logger.exception("PDF processing failed")
        st.error(f"Unable to process PDF: {exc}")
        st.stop()

    validation = validate_records(result.records, result.metadata.days_in_month)
    elapsed = time.perf_counter() - start

    log_run_summary(
        uploaded_file.name,
        elapsed,
        len(validation.valid_records),
        result.primary_count,
        result.fallback_count,
        len(result.failed_rows) + len(validation.invalid_records),
        len(validation.duplicates_dropped),
    )

    st.session_state["result"] = result
    st.session_state["validation"] = validation
    st.session_state["elapsed"] = elapsed

if "result" not in st.session_state:
    st.info("Click Process Report.")
    st.stop()

result: ParseResult = st.session_state["result"]
validation = st.session_state["validation"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Month", result.metadata.month or "—")
m2.metric("Year", result.metadata.year or "—")
m3.metric("Employees", len(validation.valid_records))
m4.metric("Processing Time", f"{st.session_state['elapsed']:.2f}s")

holiday_map = load_holidays()
configured_holidays = len(holiday_map.get(result.metadata.year or 0, {}))
if configured_holidays:
    st.success(
        f"Payroll attendance calculated with {configured_holidays} configured public holiday(s) for {result.metadata.year}. "
        "Each calendar date contributes at most one day."
    )
else:
    st.warning(
        f"No public holidays are configured for {result.metadata.year}. "
        "Sundays and 2nd/4th Saturdays will still be paid. Add that year's official holiday list to holidays.json before payroll processing."
    )

rows = [r.to_output_row() for r in validation.valid_records]
df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True)

st.download_button(
    "Download Attendance Excel",
    data=build_workbook(validation.valid_records),
    file_name=OUTPUT_WORKBOOK_NAME,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
