"""DAG integrity test: every file in dags/ must import cleanly and follow platform standards.

This is the test that would have caught most "break-it" bugs before they reached Airflow.
In CI it runs on every pull request.
"""

from pathlib import Path

import pytest

DAGS_DIR = Path(__file__).parent.parent / "dags"


@pytest.fixture(scope="module")
def dagbag():
    from airflow.dag_processing.dagbag import DagBag

    return DagBag(dag_folder=DAGS_DIR)


def test_no_import_errors(dagbag):
    assert dagbag.import_errors == {}, "\n".join(f"{k}: {v}" for k, v in dagbag.import_errors.items())


def test_at_least_one_dag(dagbag):
    assert dagbag.dags, f"no DAGs found in {DAGS_DIR}"


def test_dags_follow_platform_standards(dagbag):
    problems = []
    for dag_id, dag in dagbag.dags.items():
        if dag.catchup:
            problems.append(f"{dag_id}: catchup must be False")
        if not dag.tags:
            problems.append(f"{dag_id}: needs at least one tag")
        for task in dag.tasks:
            if task.retries < 1:
                problems.append(f"{dag_id}.{task.task_id}: retries must be >= 1")
            if task.owner in (None, "", "airflow"):
                problems.append(f"{dag_id}.{task.task_id}: needs a real owner")
    assert problems == [], "\n".join(problems)
