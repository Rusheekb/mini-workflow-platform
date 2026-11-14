"""Validates a workflow spec and returns ALL problems, each with a fix hint.

Flow:
    YAML text --(yaml.safe_load)--> dict --(Pydantic: shape)--> WorkflowSpec --(rules below)--> list[SpecError]

If the shape is wrong we stop after Pydantic (we can't look for cycles if `tasks` is missing),
but Pydantic itself already reports every shape problem at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml
from croniter import croniter
from pydantic import ValidationError

from backend.spec_models import WorkflowSpec


@dataclass(frozen=True)
class SpecError:
    path: str  # where: e.g. "tasks[1].depends_on"
    message: str  # what's wrong
    hint: str  # how to fix it

    def __str__(self) -> str:
        return f"{self.path}: {self.message} (fix: {self.hint})"


SCHEDULE_PRESETS = {"@once", "@hourly", "@daily", "@weekly", "@monthly", "@yearly"}
MEMORY_PATTERN = re.compile(r"^(\d+)(Mi|Gi)$")
MAX_MEMORY_MI = 8192  # 8Gi


# ---------------------------------------------------------------------------
# Rule functions -- YOU write these. Each takes a shape-valid WorkflowSpec and
# returns a list of SpecError (empty list = rule passed). Never raise; collect.
# ---------------------------------------------------------------------------


def check_duplicate_task_ids(spec: WorkflowSpec) -> list[SpecError]:
    """Every task id must be unique. Reports the second (and later) occurrence."""
    errors = []
    seen: set[str] = set()
    for i, task in enumerate(spec.tasks):
        if task.id in seen:
            errors.append(
                SpecError(
                    f"tasks[{i}].id",
                    f"task id {task.id!r} is used more than once",
                    "give every task a unique id",
                )
            )
        seen.add(task.id)
    return errors


def check_unknown_dependencies(spec: WorkflowSpec) -> list[SpecError]:
    """Every name in depends_on must be the id of a task in this workflow."""
    ids = {task.id for task in spec.tasks}
    errors = []
    for i, task in enumerate(spec.tasks):
        for dep in task.depends_on:
            if dep not in ids:
                errors.append(
                    SpecError(
                        f"tasks[{i}].depends_on",
                        f"task {task.id!r} depends on {dep!r}, which is not a task in this workflow",
                        f"use one of: {', '.join(sorted(ids))}",
                    )
                )
    return errors


def check_cycles(spec: WorkflowSpec) -> list[SpecError]:
    """The dependency graph must have no cycles (a -> b -> a, or a task depending on itself).

    Uses Kahn's algorithm: repeatedly "run" tasks whose dependencies are all done. If some tasks
    can never run, they are in (or waiting on) a cycle. Unknown dependencies are ignored here
    because check_unknown_dependencies already reports them.
    """
    ids = {task.id for task in spec.tasks}
    deps = {task.id: {d for d in task.depends_on if d in ids} for task in spec.tasks}

    # How many unfinished dependencies each task has, and who is waiting on each task.
    remaining = {tid: len(ds) for tid, ds in deps.items()}
    dependents: dict[str, list[str]] = {tid: [] for tid in deps}
    for tid, ds in deps.items():
        for d in ds:
            dependents[d].append(tid)

    ready = [tid for tid, n in remaining.items() if n == 0]
    done: set[str] = set()
    while ready:
        tid = ready.pop()
        done.add(tid)
        for child in dependents[tid]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)

    stuck = ids - done
    if not stuck:
        return []

    # Every stuck task waits on at least one other stuck task, so following those
    # dependencies must eventually revisit a task: that revisit is the concrete loop.
    path = [min(stuck)]
    while True:
        nxt = min(d for d in deps[path[-1]] if d in stuck)
        if nxt in path:
            loop = path[path.index(nxt) :] + [nxt]
            break
        path.append(nxt)
    order = " >> ".join(reversed(loop))  # reversed = execution order, like Airflow's `a >> b`
    return [
        SpecError(
            "tasks",
            f"dependency cycle: {order}",
            "remove one depends_on entry in this loop so the workflow has a clear starting task",
        )
    ]


def check_schedule(spec: WorkflowSpec) -> list[SpecError]:
    """schedule must be one of SCHEDULE_PRESETS or a valid cron expression."""
    if spec.schedule in SCHEDULE_PRESETS or croniter.is_valid(spec.schedule):
        return []
    return [
        SpecError(
            "schedule",
            f"{spec.schedule!r} is not a schedule preset or a valid cron expression",
            f"use one of {', '.join(sorted(SCHEDULE_PRESETS))} or cron like '0 6 * * *' (06:00 daily)",
        )
    ]


def check_image_tags(spec: WorkflowSpec) -> list[SpecError]:
    """Every image needs an explicit tag that isn't "latest", so a run is reproducible.

    Only the part after the last "/" can hold the tag: in "localhost:5000/mwp-tasks"
    the colon belongs to the registry's port, not a tag.
    """
    errors = []
    for i, task in enumerate(spec.tasks):
        name = task.image.rsplit("/", 1)[-1]
        _, has_colon, tag = name.partition(":")
        if not has_colon or not tag:
            problem = "has no tag"
        elif tag == "latest":
            problem = "uses the :latest tag, which can change under you"
        else:
            continue
        errors.append(
            SpecError(
                f"tasks[{i}].image",
                f"image {task.image!r} {problem}",
                "pin an explicit version, e.g. mwp-tasks:0.1.0",
            )
        )
    return errors


def check_memory_limits(spec: WorkflowSpec) -> list[SpecError]:
    """memory_limit must look like "512Mi" or "1Gi", be > 0, and be at most MAX_MEMORY_MI."""
    errors = []
    for i, task in enumerate(spec.tasks):
        match = MEMORY_PATTERN.match(task.memory_limit)
        if match is None:
            problem = "is not in Kubernetes format"
        else:
            amount, unit = int(match[1]), match[2]
            mebibytes = amount * 1024 if unit == "Gi" else amount
            if 0 < mebibytes <= MAX_MEMORY_MI:
                continue
            problem = f"must be between 1Mi and {MAX_MEMORY_MI // 1024}Gi"
        errors.append(
            SpecError(
                f"tasks[{i}].memory_limit",
                f"memory_limit {task.memory_limit!r} {problem}",
                "use a whole number with Mi or Gi, e.g. 512Mi or 1Gi",
            )
        )
    return errors


RULES = [
    check_duplicate_task_ids,
    check_unknown_dependencies,
    check_cycles,
    check_schedule,
    check_image_tags,
    check_memory_limits,
]


# ---------------------------------------------------------------------------
# Plumbing (written for you)
# ---------------------------------------------------------------------------


def validate_yaml(text: str) -> tuple[WorkflowSpec | None, list[SpecError]]:
    """Parse YAML text and validate it. Entry point for the API and CLI."""
    try:
        # safe_load, never yaml.load: plain load can construct arbitrary Python objects from tags.
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [SpecError("(file)", f"not valid YAML: {exc}", "check indentation and quoting")]
    if not isinstance(data, dict):
        return None, [SpecError("(file)", "spec must be a YAML mapping", "start with `workflow_name: ...`")]
    return validate_spec(data)


def validate_spec(data: dict) -> tuple[WorkflowSpec | None, list[SpecError]]:
    """Validate a parsed spec. Returns (spec, errors); spec is None if the shape is invalid."""
    try:
        spec = WorkflowSpec.model_validate(data)
    except ValidationError as exc:
        return None, _from_pydantic(exc)

    errors: list[SpecError] = []
    for rule in RULES:
        errors.extend(rule(spec))
    return spec, errors


_PYDANTIC_HINTS = {
    "missing": "add this required field",
    "extra_forbidden": "remove this field or check its spelling",
    "string_pattern_mismatch": "use only letters, digits and underscores, not starting with a digit",
    "too_short": "provide at least one item",
    "greater_than_equal": "use a larger number",
    "less_than_equal": "use a smaller number",
}


def _from_pydantic(exc: ValidationError) -> list[SpecError]:
    errors = []
    for err in exc.errors():
        path = ""
        for part in err["loc"]:
            path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
        hint = _PYDANTIC_HINTS.get(err["type"], "check the value's type and format")
        errors.append(SpecError(path or "(root)", err["msg"], hint))
    return errors
