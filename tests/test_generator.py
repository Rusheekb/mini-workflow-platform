"""Tests for backend/generator.py.

We don't just compare text: we load the generated file with Airflow's own DagBag (the same parser
the scheduler uses) and inspect the real DAG object.
"""

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from backend.generator import (
    DagGenerator,
    KubernetesTaskRenderer,
    LocalTaskRenderer,
    TaskRenderer,
    main,
)
from backend.validator import validate_spec, validate_yaml

SPEC_FILE = Path(__file__).parent.parent / "specs" / "valid" / "daily_exchange_rates.yaml"


@pytest.fixture
def spec():
    spec, errors = validate_yaml(SPEC_FILE.read_text())
    assert errors == []
    return spec


def load_dag(tmp_path, spec, renderer):
    """Write the DAG with `renderer`, parse it with DagBag, return the DAG object."""
    from airflow.dag_processing.dagbag import DagBag

    DagGenerator(renderer).write(spec, tmp_path)
    bag = DagBag(dag_folder=tmp_path)
    assert bag.import_errors == {}, bag.import_errors
    return bag.dags[spec.workflow_name]


# --- OOP structure -------------------------------------------------------------


def test_base_renderer_cannot_be_instantiated():
    with pytest.raises(TypeError):
        TaskRenderer()


@pytest.mark.parametrize("renderer", [LocalTaskRenderer(), KubernetesTaskRenderer()])
def test_generated_source_is_valid_python(spec, renderer):
    ast.parse(DagGenerator(renderer).generate(spec))


# --- DAG-level settings (same for every target) --------------------------------


@pytest.mark.parametrize("renderer", [LocalTaskRenderer(), KubernetesTaskRenderer()])
def test_dag_settings_come_from_spec(tmp_path, spec, renderer):
    dag = load_dag(tmp_path, spec, renderer)
    assert dag.catchup is False
    assert dag.start_date.year == 2026 and dag.start_date.month == 1 and dag.start_date.day == 1
    assert {"finance", "example", "generated", f"target:{renderer.target}"} <= set(dag.tags)
    task = dag.get_task("extract")
    assert task.owner == "rusheek"
    assert task.retries == 3
    assert task.retry_delay == timedelta(minutes=5)


def test_dependencies_are_wired(tmp_path, spec):
    dag = load_dag(tmp_path, spec, LocalTaskRenderer())
    assert dag.get_task("extract").upstream_task_ids == set()
    assert dag.get_task("transform").upstream_task_ids == {"extract"}
    assert dag.get_task("load").upstream_task_ids == {"transform"}


def test_generated_code_never_uses_now(spec):
    source = DagGenerator(LocalTaskRenderer()).generate(spec)
    assert "now(" not in source  # a moving start_date breaks scheduling


# --- target-specific -----------------------------------------------------------


def test_local_target_uses_bash_operator(tmp_path, spec):
    dag = load_dag(tmp_path, spec, LocalTaskRenderer(tasks_dir="/opt/airflow/tasks"))
    task = dag.get_task("extract")
    assert type(task).__name__ == "BashOperator"
    assert task.bash_command == "cd /opt/airflow/tasks && python extract.py"
    assert task.env == {"RUN_DATE": "{{ ds }}"} and task.append_env is True


def test_kubernetes_target_sets_image_and_memory(tmp_path, spec):
    dag = load_dag(tmp_path, spec, KubernetesTaskRenderer(namespace="airflow"))
    task = dag.get_task("extract")
    assert type(task).__name__ == "KubernetesPodOperator"
    assert task.image == "mwp-tasks:0.1.0"
    assert task.cmds == ["python"] and task.arguments == ["extract.py"]
    assert [(e.name, e.value) for e in task.env_vars] == [("RUN_DATE", "{{ ds }}")]
    assert task.env_from[0].secret_ref.name == "mwp-task-env"
    assert task.container_resources.limits == {"memory": "512Mi"}
    assert task.container_resources.requests == {"memory": "512Mi"}
    assert dag.get_task("transform").container_resources.limits == {"memory": "256Mi"}  # default


def test_pod_names_are_kubernetes_safe(spec):
    for task in spec.tasks:
        name = KubernetesTaskRenderer.pod_name(spec, task)
        assert name == name.lower() and "_" not in name


# --- safety --------------------------------------------------------------------


def test_spec_values_cannot_inject_code(tmp_path):
    evil_owner = "x', 'retries': 99}) or __import__('os').system('echo pwned') #"
    spec, errors = validate_spec(
        {
            "workflow_name": "safe",
            "schedule": "@daily",
            "owner": evil_owner,
            "tasks": [{"id": "a", "image": "img:1.0", "command": ["echo", "hi; rm -rf /"]}],
        }
    )
    assert errors == []
    dag = load_dag(tmp_path, spec, LocalTaskRenderer())
    task = dag.get_task("a")
    assert task.owner == evil_owner  # stored as plain text, not executed
    assert task.retries == 0
    assert task.bash_command.endswith("echo 'hi; rm -rf /'")  # shell-quoted, so `;` is just text


def test_write_is_atomic_and_named_after_workflow(tmp_path, spec):
    path = DagGenerator(LocalTaskRenderer()).write(spec, tmp_path)
    assert path == tmp_path / "daily_exchange_rates.py"
    assert [p.name for p in tmp_path.iterdir()] == ["daily_exchange_rates.py"]  # no .tmp left behind


# --- CLI -----------------------------------------------------------------------


def test_cli_refuses_invalid_spec(tmp_path, capsys):
    bad = Path(__file__).parent.parent / "specs" / "invalid" / "cycle.yaml"
    assert main([str(bad), "--out", str(tmp_path)]) == 1
    assert "cycle" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_cli_writes_dag(tmp_path):
    assert main([str(SPEC_FILE), "--target", "kubernetes", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "daily_exchange_rates.py").exists()
