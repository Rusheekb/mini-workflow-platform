"""Tests for backend/api.py.

FastAPI's dependency injection lets us swap the real settings and Airflow client for test versions:
files go to a temp folder and Airflow is a fake object -- no Docker needed.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.airflow_client import AirflowUnavailable
from backend.api import app, get_airflow, get_settings
from backend.config import Settings

VALID_YAML = (Path(__file__).parent.parent / "specs" / "valid" / "daily_exchange_rates.yaml").read_text()


class FakeAirflow:
    def __init__(self, runs=None, down=False):
        self.runs = runs or {}
        self.down = down

    def recent_runs(self, dag_id, limit=5):
        if self.down:
            raise AirflowUnavailable("connection refused")
        return self.runs.get(dag_id, [])[:limit]


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        dags_dir=tmp_path / "dags",
        specs_dir=tmp_path / "specs",
        default_target="local",
        airflow_url="http://fake",
        airflow_username="u",
        airflow_password="p",
    )
    fake = FakeAirflow()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_airflow] = lambda: fake
    yield TestClient(app), settings, fake
    app.dependency_overrides.clear()


def test_health(setup):
    client, _, _ = setup
    assert client.get("/health").json() == {"status": "ok"}


def test_create_valid_workflow_returns_201_and_writes_files(setup):
    client, settings, _ = setup
    response = client.post("/workflows", json={"spec_yaml": VALID_YAML})
    assert response.status_code == 201
    assert response.json()["workflow_name"] == "daily_exchange_rates"
    assert (settings.dags_dir / "daily_exchange_rates.py").exists()
    assert (settings.specs_dir / "daily_exchange_rates.yaml").read_text() == VALID_YAML


def test_create_kubernetes_target(setup):
    client, settings, _ = setup
    response = client.post("/workflows", json={"spec_yaml": VALID_YAML, "target": "kubernetes"})
    assert response.status_code == 201
    assert "KubernetesPodOperator" in (settings.dags_dir / "daily_exchange_rates.py").read_text()


def test_invalid_spec_returns_400_with_all_errors_and_writes_nothing(setup):
    client, settings, _ = setup
    bad = (Path(__file__).parent.parent / "specs" / "invalid" / "many_problems.yaml").read_text()
    response = client.post("/workflows", json={"spec_yaml": bad})
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert len(errors) == 5
    assert all({"path", "message", "hint"} <= e.keys() for e in errors)
    assert not settings.dags_dir.exists()


def test_duplicate_returns_409_unless_overwrite(setup):
    client, _, _ = setup
    assert client.post("/workflows", json={"spec_yaml": VALID_YAML}).status_code == 201
    assert client.post("/workflows", json={"spec_yaml": VALID_YAML}).status_code == 409
    assert client.post("/workflows", json={"spec_yaml": VALID_YAML, "overwrite": True}).status_code == 201


def test_unknown_target_is_rejected_by_fastapi(setup):
    client, _, _ = setup
    assert client.post("/workflows", json={"spec_yaml": VALID_YAML, "target": "mainframe"}).status_code == 422


def test_list_includes_latest_run(setup):
    client, _, fake = setup
    client.post("/workflows", json={"spec_yaml": VALID_YAML})
    fake.runs["daily_exchange_rates"] = [{"dag_run_id": "r2", "state": "success"}, {"dag_run_id": "r1"}]
    body = client.get("/workflows").json()
    assert body["airflow_available"] is True
    [wf] = body["workflows"]
    assert wf["task_count"] == 3
    assert wf["latest_run"]["run_id"] == "r2" and wf["latest_run"]["state"] == "success"


def test_list_still_works_when_airflow_is_down(setup):
    client, _, fake = setup
    client.post("/workflows", json={"spec_yaml": VALID_YAML})
    fake.down = True
    body = client.get("/workflows").json()
    assert body["airflow_available"] is False
    assert body["workflows"][0]["latest_run"] is None


def test_get_one_workflow(setup):
    client, _, fake = setup
    client.post("/workflows", json={"spec_yaml": VALID_YAML})
    fake.runs["daily_exchange_rates"] = [{"dag_run_id": "r1", "state": "failed"}]
    body = client.get("/workflows/daily_exchange_rates").json()
    assert body["tasks"] == ["extract", "transform", "load"]
    assert body["recent_runs"][0]["state"] == "failed"


def test_get_missing_workflow_is_404(setup):
    client, _, _ = setup
    assert client.get("/workflows/nope").status_code == 404


def test_path_traversal_is_rejected(setup):
    client, _, _ = setup
    assert client.get("/workflows/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)
    assert client.get("/workflows/bad-name!").status_code == 422
