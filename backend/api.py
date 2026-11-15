"""REST API for the Mini Workflow Platform.

    POST /workflows          validate a YAML spec -> generate the DAG -> save both    (201 / 400 / 409)
    GET  /workflows          list workflows with their latest Airflow run status
    GET  /workflows/{name}   one workflow: spec + recent runs
    GET  /health             liveness check

Run:   uvicorn backend.api:app --reload --port 8000      then open http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, Literal

import yaml
from fastapi import Depends, FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.airflow_client import AirflowClient, AirflowUnavailable
from backend.config import Settings
from backend.generator import RENDERERS, DagGenerator
from backend.spec_models import IDENTIFIER
from backend.validator import validate_yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mwp.api")

app = FastAPI(
    title="Mini Workflow Platform",
    version="0.1.0",
    description="Submit a YAML workflow spec; the platform validates it and generates an Airflow DAG.",
)

# CORS: browsers block a page on one origin (the React dev server, :5173) from calling another (:8000)
# unless the API explicitly allows it. Allow only our frontend, only the methods we use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# --- dependencies (FastAPI injects these; tests swap them for fakes) -----------------------------


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache
def get_airflow() -> AirflowClient:
    s = get_settings()
    return AirflowClient(s.airflow_url, s.airflow_username, s.airflow_password)


SettingsDep = Annotated[Settings, Depends(get_settings)]
AirflowDep = Annotated[AirflowClient, Depends(get_airflow)]


# --- request / response models (these also generate the Swagger docs) ---------------------------


class SubmitWorkflow(BaseModel):
    spec_yaml: str = Field(max_length=100_000, description="The workflow spec as YAML text")
    target: Literal["local", "kubernetes"] | None = Field(None, description="Defaults to the server's target")
    overwrite: bool = Field(False, description="Replace an existing workflow with the same name")


class ErrorItem(BaseModel):
    path: str
    message: str
    hint: str


class ValidationFailed(BaseModel):
    errors: list[ErrorItem]


class WorkflowCreated(BaseModel):
    workflow_name: str
    target: str
    dag_file: str
    message: str


class RunInfo(BaseModel):
    run_id: str
    state: str | None
    run_type: str | None = None
    logical_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class WorkflowSummary(BaseModel):
    workflow_name: str
    owner: str
    schedule: str
    task_count: int
    latest_run: RunInfo | None


class WorkflowList(BaseModel):
    airflow_available: bool
    workflows: list[WorkflowSummary]


class WorkflowDetail(BaseModel):
    workflow_name: str
    spec_yaml: str
    tasks: list[str]
    airflow_available: bool
    recent_runs: list[RunInfo]


# --- helpers -------------------------------------------------------------------------------------


def _to_run(raw: dict) -> RunInfo:
    return RunInfo(
        run_id=raw["dag_run_id"],
        state=raw.get("state"),
        run_type=raw.get("run_type"),
        logical_date=raw.get("logical_date"),
        start_date=raw.get("start_date"),
        end_date=raw.get("end_date"),
    )


def _recent_runs(airflow: AirflowClient, dag_id: str, limit: int) -> tuple[bool, list[RunInfo]]:
    """(airflow_available, runs). If Airflow is down we still answer, just without run status."""
    try:
        return True, [_to_run(r) for r in airflow.recent_runs(dag_id, limit=limit)]
    except AirflowUnavailable:
        return False, []


# --- endpoints -----------------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/workflows",
    status_code=201,
    response_model=WorkflowCreated,
    responses={400: {"model": ValidationFailed}, 409: {"description": "Workflow already exists"}},
)
def create_workflow(body: SubmitWorkflow, settings: SettingsDep):
    spec, errors = validate_yaml(body.spec_yaml)
    if errors:
        log.info("rejected spec with %d error(s)", len(errors))
        return JSONResponse(
            status_code=400,
            content={"errors": [{"path": e.path, "message": e.message, "hint": e.hint} for e in errors]},
        )

    spec_file = settings.specs_dir / f"{spec.workflow_name}.yaml"
    if spec_file.exists() and not body.overwrite:
        raise HTTPException(
            409, f"workflow {spec.workflow_name!r} already exists; set overwrite=true to replace it"
        )

    target = body.target or settings.default_target
    dag_file = DagGenerator(RENDERERS[target]()).write(spec, settings.dags_dir)
    settings.specs_dir.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(body.spec_yaml)  # keep the source spec: it, not the DAG file, is the source of truth

    log.info("created workflow %s (target=%s) -> %s", spec.workflow_name, target, dag_file)
    return WorkflowCreated(
        workflow_name=spec.workflow_name,
        target=target,
        dag_file=dag_file.name,
        message="DAG generated. Airflow will pick it up within ~30 seconds (new DAGs start paused).",
    )


@app.get("/workflows", response_model=WorkflowList)
def list_workflows(settings: SettingsDep, airflow: AirflowDep):
    summaries = []
    airflow_ok = True
    for spec_file in sorted(settings.specs_dir.glob("*.yaml")):
        spec, errors = validate_yaml(spec_file.read_text())
        if errors:  # shouldn't happen (we only save valid specs), but never let one bad file break the list
            log.warning("skipping invalid stored spec %s", spec_file.name)
            continue
        latest = None
        if airflow_ok:  # once Airflow is down, don't wait on a timeout for every workflow
            airflow_ok, runs = _recent_runs(airflow, spec.workflow_name, limit=1)
            latest = runs[0] if runs else None
        summaries.append(
            WorkflowSummary(
                workflow_name=spec.workflow_name,
                owner=spec.owner,
                schedule=spec.schedule,
                task_count=len(spec.tasks),
                latest_run=latest,
            )
        )
    return WorkflowList(airflow_available=airflow_ok, workflows=summaries)


@app.get("/workflows/{name}", response_model=WorkflowDetail, responses={404: {"description": "Not found"}})
def get_workflow(
    settings: SettingsDep,
    airflow: AirflowDep,
    # The pattern blocks path traversal like "../../etc/passwd" before we ever touch the filesystem.
    name: str = PathParam(pattern=IDENTIFIER, max_length=200),
):
    spec_file = settings.specs_dir / f"{name}.yaml"
    if not spec_file.exists():
        raise HTTPException(404, f"workflow {name!r} not found")
    spec_yaml = spec_file.read_text()
    data = yaml.safe_load(spec_yaml)
    airflow_ok, runs = _recent_runs(airflow, name, limit=5)
    return WorkflowDetail(
        workflow_name=name,
        spec_yaml=spec_yaml,
        tasks=[t["id"] for t in data.get("tasks", [])],
        airflow_available=airflow_ok,
        recent_runs=runs,
    )
