"""Shared helpers for the task scripts: logging, run date, storage, and secrets.

Storage: if MWP_S3_BUCKET is set, keys go to S3 (s3://bucket/raw/2026-11-13/rates.json); otherwise to local
files under MWP_DATA_DIR. The task scripts don't know or care which -- only this file changes between M3 and M5.

Credentials: boto3 finds them on its own (env vars locally; on EKS, IRSA / Pod Identity gives the pod temporary
credentials, so no keys exist at all). Nothing here reads or logs a key.
"""

import logging
import os
from datetime import date
from pathlib import Path

DATA_DIR = Path(os.environ.get("MWP_DATA_DIR", "/opt/airflow/data"))
BUCKET = os.environ.get("MWP_S3_BUCKET", "")
USE_S3 = bool(BUCKET) and BUCKET != "replace-me"
SECRET_NAME = os.environ.get("MWP_SECRET_NAME", "")


def get_logger(name: str) -> logging.Logger:
    # Airflow captures stdout/stderr into the task log, so plain logging shows up in the UI.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    return logging.getLogger(name)


def run_date() -> str:
    """The date this run is processing, from Airflow's {{ ds }} (falls back to today for manual runs)."""
    value = os.environ.get("RUN_DATE") or date.today().isoformat()
    date.fromisoformat(value)  # raises ValueError on garbage instead of silently writing to a bad key
    return value


def _client(service: str):
    import boto3  # imported lazily so local-only runs don't need AWS configured

    return boto3.client(service)


def write_text(key: str, text: str) -> str:
    """Write `text` under `key`. Idempotent: a re-run of the same date overwrites the same object/file."""
    if USE_S3:
        # A PUT is atomic in S3: readers see the old object or the new one, never half of one.
        _client("s3").put_object(Bucket=BUCKET, Key=key, Body=text.encode("utf-8"))
        return f"s3://{BUCKET}/{key}"

    path = DATA_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)  # atomic rename, same guarantee as S3
    return str(path)


def read_text(key: str) -> str:
    if USE_S3:
        from botocore.exceptions import ClientError

        try:
            obj = _client("s3").get_object(Bucket=BUCKET, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(
                    f"s3://{BUCKET}/{key} not found -- did the upstream task run for this date?"
                ) from None
            raise  # AccessDenied etc.: let it fail loudly with AWS's own message
        return obj["Body"].read().decode("utf-8")

    path = DATA_DIR / key
    try:
        return path.read_text()
    except FileNotFoundError:
        raise FileNotFoundError(f"{path} not found -- did the upstream task run for this date?") from None


def get_secret(name: str) -> str:
    """Fetch a secret's value from AWS Secrets Manager at run time (never stored in code, the DAG, or logs)."""
    from botocore.exceptions import ClientError

    try:
        return _client("secretsmanager").get_secret_value(SecretId=name)["SecretString"]
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        raise RuntimeError(f"could not read secret {name!r} from Secrets Manager: {code}") from exc
