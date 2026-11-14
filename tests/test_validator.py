"""Tests for backend/validator.py. Run: pytest -v"""

import copy
from pathlib import Path

import pytest

from backend.validator import validate_spec, validate_yaml

SPECS = Path(__file__).parent.parent / "specs"

VALID = {
    "workflow_name": "wf",
    "schedule": "@daily",
    "owner": "rusheek",
    "tasks": [
        {"id": "a", "image": "img:1.0", "command": ["echo", "a"]},
        {"id": "b", "image": "img:1.0", "command": ["echo", "b"], "depends_on": ["a"]},
    ],
}


def make(**changes):
    """A valid spec dict with top-level fields overridden."""
    spec = copy.deepcopy(VALID)
    spec.update(changes)
    return spec


def task(id, deps=(), image="img:1.0", memory="256Mi"):
    return {
        "id": id,
        "image": image,
        "command": ["echo", id],
        "depends_on": list(deps),
        "memory_limit": memory,
    }


def paths(errors):
    return [e.path for e in errors]


# --- the happy path -----------------------------------------------------------


def test_minimal_valid_spec_has_no_errors():
    spec, errors = validate_spec(make())
    assert errors == []
    assert spec.workflow_name == "wf"


def test_example_spec_file_is_valid():
    spec, errors = validate_yaml((SPECS / "valid" / "daily_exchange_rates.yaml").read_text())
    assert errors == [], [str(e) for e in errors]
    assert [t.id for t in spec.tasks] == ["extract", "transform", "load"]


# --- shape errors (Pydantic) ---------------------------------------------------


def test_missing_required_field():
    data = make()
    del data["owner"]
    _, errors = validate_spec(data)
    assert "owner" in paths(errors)


def test_typo_field_is_rejected_not_ignored():
    data = make(tasks=[{"id": "a", "image": "img:1.0", "command": ["x"], "depend_on": []}])
    _, errors = validate_spec(data)
    assert "tasks[0].depend_on" in paths(errors)


def test_invalid_yaml_is_reported_not_raised():
    _, errors = validate_yaml("tasks: [unclosed")
    assert paths(errors) == ["(file)"]


# --- duplicate IDs -------------------------------------------------------------


def test_duplicate_task_ids():
    _, errors = validate_spec(make(tasks=[task("a"), task("b"), task("a")]))
    assert paths(errors) == ["tasks[2].id"]


# --- unknown dependencies ------------------------------------------------------


def test_unknown_dependency():
    _, errors = validate_spec(make(tasks=[task("a"), task("b", deps=["a", "ghost"])]))
    assert paths(errors) == ["tasks[1].depends_on"]
    assert "ghost" in errors[0].message


# --- cycles --------------------------------------------------------------------


def _cycle_errors(errors):
    return [e for e in errors if e.path == "tasks" and "cycle" in e.message.lower()]


def test_self_dependency_is_a_cycle():
    _, errors = validate_spec(make(tasks=[task("a", deps=["a"])]))
    assert len(_cycle_errors(errors)) == 1


def test_two_task_cycle():
    _, errors = validate_spec(make(tasks=[task("a", deps=["b"]), task("b", deps=["a"])]))
    assert len(_cycle_errors(errors)) >= 1


def test_three_task_cycle():
    tasks = [task("a", deps=["c"]), task("b", deps=["a"]), task("c", deps=["b"])]
    _, errors = validate_spec(make(tasks=tasks))
    assert len(_cycle_errors(errors)) >= 1


def test_diamond_is_not_a_cycle():
    #    a
    #   / \
    #  b   c      d depends on both b and c -- shared ancestor, but no loop.
    #   \ /
    #    d
    tasks = [task("a"), task("b", deps=["a"]), task("c", deps=["a"]), task("d", deps=["b", "c"])]
    _, errors = validate_spec(make(tasks=tasks))
    assert errors == []


def test_cycle_check_does_not_crash_on_unknown_dependency():
    _, errors = validate_spec(make(tasks=[task("a", deps=["ghost"])]))
    assert paths(errors) == ["tasks[0].depends_on"]


# --- schedule ------------------------------------------------------------------


@pytest.mark.parametrize("schedule", ["@daily", "@hourly", "@once", "0 6 * * *", "*/15 9-17 * * 1-5"])
def test_valid_schedules(schedule):
    _, errors = validate_spec(make(schedule=schedule))
    assert errors == []


@pytest.mark.parametrize("schedule", ["daily", "@every_day", "61 * * * *", "* * *", ""])
def test_invalid_schedules(schedule):
    _, errors = validate_spec(make(schedule=schedule))
    assert paths(errors) == ["schedule"]


# --- image tags ----------------------------------------------------------------


@pytest.mark.parametrize(
    "image",
    [
        "mwp-tasks:0.1.0",
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/mwp-tasks:0.1.0",
        "localhost:5000/mwp-tasks:1.2",
    ],
)
def test_pinned_images_are_accepted(image):
    _, errors = validate_spec(make(tasks=[task("a", image=image)]))
    assert errors == []


@pytest.mark.parametrize("image", ["mwp-tasks", "mwp-tasks:latest", "localhost:5000/mwp-tasks", "mwp-tasks:"])
def test_unpinned_images_are_rejected(image):
    _, errors = validate_spec(make(tasks=[task("a", image=image)]))
    assert paths(errors) == ["tasks[0].image"]


# --- memory limits -------------------------------------------------------------


@pytest.mark.parametrize("memory", ["128Mi", "512Mi", "1Gi", "8Gi"])
def test_valid_memory_limits(memory):
    _, errors = validate_spec(make(tasks=[task("a", memory=memory)]))
    assert errors == []


@pytest.mark.parametrize("memory", ["512MB", "512", "0Mi", "64Gi", "abc", "1.5Gi"])
def test_invalid_memory_limits(memory):
    _, errors = validate_spec(make(tasks=[task("a", memory=memory)]))
    assert paths(errors) == ["tasks[0].memory_limit"]


# --- reports everything, with hints --------------------------------------------


def test_reports_all_errors_not_just_the_first():
    _, errors = validate_yaml((SPECS / "invalid" / "many_problems.yaml").read_text())
    assert sorted(paths(errors)) == sorted(
        ["schedule", "tasks[0].image", "tasks[0].memory_limit", "tasks[1].id", "tasks[1].image"]
    )
    assert all(e.hint for e in errors), "every error needs a fix hint"


@pytest.mark.parametrize(
    "filename, expected_path",
    [
        ("cycle.yaml", "tasks"),
        ("unknown_dependency.yaml", "tasks[1].depends_on"),
        ("typo_field.yaml", "tasks[1].depend_on"),
    ],
)
def test_invalid_spec_files(filename, expected_path):
    _, errors = validate_yaml((SPECS / "invalid" / filename).read_text())
    assert expected_path in paths(errors)
