"""
logger.py

Central logging setup. Everything funnels into logs/app.log so a run can be
audited after the fact — timestamps, PDF name, per-employee parse method,
duplicates, exceptions.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager

from loguru import logger

from config import LOG_DIR, LOG_FILE

LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.remove()  # drop default stderr handler, we configure our own
logger.add(
    LOG_FILE,
    rotation="5 MB",
    retention=10,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    enqueue=True,
)
# Also echo to stderr at WARNING+ so `streamlit run` consoles show problems.
logger.add(sys.stderr, level="WARNING", format="{time:HH:mm:ss} | {level} | {message}")


@contextmanager
def timed_step(label: str):
    """Log how long a processing step took."""
    start = time.perf_counter()
    logger.info(f"Start: {label}")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logger.info(f"Done: {label} ({elapsed:.3f}s)")


def log_run_summary(
    pdf_name: str,
    processing_time_s: float,
    employees_parsed: int,
    primary_count: int,
    fallback_count: int,
    failed_rows: int,
    duplicate_rows: int,
) -> None:
    logger.info(
        "Run summary | file={} | time={:.3f}s | parsed={} | primary={} | "
        "fallback={} | failed={} | duplicates={}".format(
            pdf_name,
            processing_time_s,
            employees_parsed,
            primary_count,
            fallback_count,
            failed_rows,
            duplicate_rows,
        )
    )
