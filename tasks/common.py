"""Shared helpers for the task scripts: logging, run date, and storage.

Storage keys look like S3 keys ("raw/2026-11-13/rates.json"), so switching from local files (M3)
to S3 (M5) only changes this file, not the task scripts.
"""

import logging
import os
from datetime import date
from pathlib import Path

DATA_DIR = Path(os.environ.get("MWP_DATA_DIR", "/opt/airflow/data"))


def get_logger(name: str) -> logging.Logger:
    # Airflow captures stdout/stderr into the task log, so plain logging shows up in the UI.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return logging.getLogger(name)


def run_date() -> str:
    """The date this run is processing, from Airflow's {{ ds }} (falls back to today for manual runs)."""
    value = os.environ.get("RUN_DATE") or date.today().isoformat()
    date.fromisoformat(value)  # raises ValueError on garbage instead of silently writing to a bad key
    return value


def write_text(key: str, text: str) -> str:
    """Write `text` under `key`. Idempotent: a re-run of the same date overwrites the same file."""
    path = DATA_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)  # atomic: readers never see half a file
    return str(path)


def read_text(key: str) -> str:
    path = DATA_DIR / key
    try:
        return path.read_text()
    except FileNotFoundError:
        raise FileNotFoundError(f"{path} not found -- did the upstream task run for this date?") from None
