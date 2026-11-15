"""Tests for tasks/common.py storage helpers, local and S3 (with a fake S3 client -- no AWS needed)."""

import importlib
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent / "tasks"))
common = importlib.import_module("common")


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "nope"}}, "GetObject")

        class _Body:
            def __init__(self, data):
                self.data = data

            def read(self):
                return self.data

        return {"Body": _Body(self.objects[(Bucket, Key)])}


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "USE_S3", False)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(common, "USE_S3", True)
    monkeypatch.setattr(common, "BUCKET", "test-bucket")
    monkeypatch.setattr(common, "_client", lambda service: fake)
    return fake


def test_local_write_is_idempotent(local):
    common.write_text("raw/2026-11-13/x.json", "first")
    common.write_text("raw/2026-11-13/x.json", "second")  # re-run of the same date
    assert common.read_text("raw/2026-11-13/x.json") == "second"
    assert [p.name for p in (local / "raw" / "2026-11-13").iterdir()] == ["x.json"]  # no dupes, no .tmp


def test_local_missing_key_has_helpful_error(local):
    with pytest.raises(FileNotFoundError, match="did the upstream task run"):
        common.read_text("raw/1999-01-01/x.json")


def test_s3_round_trip(s3):
    location = common.write_text("processed/2026-11-13/rates.csv", "a,b\n1,2\n")
    assert location == "s3://test-bucket/processed/2026-11-13/rates.csv"
    assert common.read_text("processed/2026-11-13/rates.csv") == "a,b\n1,2\n"


def test_s3_missing_key_becomes_file_not_found(s3):
    with pytest.raises(FileNotFoundError, match="s3://test-bucket/raw/1999-01-01/x.json"):
        common.read_text("raw/1999-01-01/x.json")


def test_run_date_rejects_garbage(monkeypatch):
    monkeypatch.setenv("RUN_DATE", "not-a-date")
    with pytest.raises(ValueError):
        common.run_date()
