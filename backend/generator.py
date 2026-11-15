"""Turns a validated WorkflowSpec into an Airflow DAG file.

Design (template method + strategy):
    TaskRenderer (abstract)          -- knows HOW to turn a task into operator source code
      |- LocalTaskRenderer           -- BashOperator: runs the command on the Airflow worker (Docker Compose)
      |- KubernetesTaskRenderer      -- KubernetesPodOperator: runs each task in its own pod (kind / EKS)
    DagGenerator(renderer)           -- builds the whole file; works with ANY renderer

Usage:
    python -m backend.generator specs/valid/daily_exchange_rates.yaml --target local --out dags/
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from backend.spec_models import TaskSpec, WorkflowSpec
from backend.validator import validate_yaml

# A FIXED start date. Never datetime.now(): a moving start_date means the scheduler never sees a
# complete interval, so the DAG may never run.
START_DATE = (2026, 1, 1)


def py(value: object) -> str:
    """Python source for a literal value. repr() escapes quotes, so spec values can't inject code."""
    return repr(value)


class TaskRenderer(ABC):
    """Turns one TaskSpec into Python source for an Airflow operator.

    Subclasses fill in three things: the operator class name, its import line, and its arguments.
    `render()` (the template method) is shared, so every target produces consistently shaped code.
    """

    target: str  # name used on the CLI / API, e.g. "local"

    # Every task gets the run's logical date. Airflow renders "{{ ds }}" (a Jinja template) to e.g.
    # "2026-11-13" at run time, so a re-run of the same date overwrites the same output (idempotent).
    RUN_ENV: ClassVar[dict[str, str]] = {"RUN_DATE": "{{ ds }}"}

    @property
    @abstractmethod
    def operator_name(self) -> str: ...

    @property
    @abstractmethod
    def imports(self) -> tuple[str, ...]: ...

    @abstractmethod
    def operator_kwargs(self, task: TaskSpec, spec: WorkflowSpec) -> dict[str, str]:
        """Keyword arguments for the operator; values are already Python source (use py())."""

    def render(self, task: TaskSpec, spec: WorkflowSpec) -> str:
        kwargs = {"task_id": py(task.id), **self.operator_kwargs(task, spec)}
        lines = [f"    {task.id} = {self.operator_name}("]
        lines += [f"        {key}={value}," for key, value in kwargs.items()]
        lines.append("    )")
        return "\n".join(lines)


class LocalTaskRenderer(TaskRenderer):
    """Runs the task's command with BashOperator inside the Airflow worker.

    The `image` and `memory_limit` fields are NOT enforced here -- there is no container per task.
    That's fine for local development; use the Kubernetes target for isolation and limits.
    """

    target = "local"
    operator_name = "BashOperator"
    imports = ("from airflow.providers.standard.operators.bash import BashOperator",)

    def __init__(self, tasks_dir: str = "/opt/airflow/tasks"):
        self.tasks_dir = tasks_dir

    def operator_kwargs(self, task: TaskSpec, spec: WorkflowSpec) -> dict[str, str]:
        # shlex.join quotes each argument for the shell, so a spec can't smuggle in `; rm -rf /`.
        bash_command = f"cd {shlex.quote(self.tasks_dir)} && {shlex.join(task.command)}"
        return {
            "bash_command": py(bash_command),
            "env": py(self.RUN_ENV),
            "append_env": "True",  # keep the worker's own env (PATH, AWS settings) and add ours
        }


class KubernetesTaskRenderer(TaskRenderer):
    """Runs each task as its own Kubernetes pod with the spec's image and memory limit."""

    target = "kubernetes"
    operator_name = "KubernetesPodOperator"
    imports = (
        "from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator",
        "from kubernetes.client import models as k8s",
    )

    def __init__(self, namespace: str = "airflow", env_secret: str | None = "mwp-task-env"):
        self.namespace = namespace
        # Pods don't inherit Airflow's environment. Settings like the S3 bucket and (locally) AWS keys come
        # from this Kubernetes Secret. On EKS the keys would be dropped entirely in favor of IRSA.
        self.env_secret = env_secret

    @staticmethod
    def pod_name(spec: WorkflowSpec, task: TaskSpec) -> str:
        # Kubernetes names allow only lowercase letters, digits and "-" (no underscores).
        name = re.sub(r"[^a-z0-9-]", "-", f"{spec.workflow_name}-{task.id}".lower())
        return name[:50].strip("-")

    def operator_kwargs(self, task: TaskSpec, spec: WorkflowSpec) -> dict[str, str]:
        memory = py({"memory": task.memory_limit})
        kwargs = {
            "name": py(self.pod_name(spec, task)),
            "namespace": py(self.namespace),
            "image": py(task.image),
            "image_pull_policy": py("IfNotPresent"),
            "cmds": py(task.command[:1]),
            "arguments": py(task.command[1:]),
            "env_vars": py(self.RUN_ENV),
            # requests = what the scheduler reserves; limits = the hard cap (exceed it -> OOMKilled).
            "container_resources": f"k8s.V1ResourceRequirements(requests={memory}, limits={memory})",
            "get_logs": "True",
            # Keep failed pods around so you can `kubectl describe` / `kubectl logs` them.
            "on_finish_action": py("delete_succeeded_pod"),
        }
        if self.env_secret:
            # optional=True: the pod still starts if the Secret doesn't exist (tasks fall back to local files).
            kwargs["env_from"] = (
                "[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource("
                f"name={py(self.env_secret)}, optional=True))]"
            )
        return kwargs


RENDERERS: dict[str, type[TaskRenderer]] = {
    LocalTaskRenderer.target: LocalTaskRenderer,
    KubernetesTaskRenderer.target: KubernetesTaskRenderer,
}


class DagGenerator:
    """Builds a complete DAG file from a spec, using whichever renderer it's given."""

    def __init__(self, renderer: TaskRenderer):
        self.renderer = renderer

    def generate(self, spec: WorkflowSpec) -> str:
        """Return the DAG file's source code. `spec` must already be validated."""
        tags = [*spec.tags, "generated", f"target:{self.renderer.target}"]
        header = [
            f"# GENERATED by mini-workflow-platform from spec {spec.workflow_name!r} ({self.renderer.target}).",
            "# Do not edit by hand: change the spec and regenerate.",
            "from datetime import timedelta",
            "",
            "import pendulum",
            "from airflow.sdk import DAG",
            *self.renderer.imports,
            "",
            "with DAG(",
            f"    dag_id={py(spec.workflow_name)},",
            f"    schedule={py(spec.schedule)},",
            f"    start_date=pendulum.datetime({START_DATE[0]}, {START_DATE[1]}, {START_DATE[2]}, tz='UTC'),",
            "    catchup=False,",
            "    default_args={",
            f"        'owner': {py(spec.owner)},",
            f"        'retries': {spec.retries},",
            f"        'retry_delay': timedelta(minutes={spec.retry_delay_minutes}),",
            "    },",
            f"    tags={py(tags)},",
            ") as dag:",
        ]
        tasks = [self.renderer.render(task, spec) for task in spec.tasks]
        edges = [f"    {dep} >> {task.id}" for task in spec.tasks for dep in task.depends_on]
        body = "\n\n".join(tasks)
        if edges:
            body += "\n\n" + "\n".join(edges)
        return "\n".join(header) + "\n" + body + "\n"

    def write(self, spec: WorkflowSpec, out_dir: Path) -> Path:
        """Write the DAG file atomically so Airflow never parses a half-written file."""
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{spec.workflow_name}.py"
        tmp = path.with_suffix(".py.tmp")  # Airflow only parses *.py, so it ignores this
        tmp.write_text(self.generate(spec))
        tmp.replace(path)  # rename is atomic on the same filesystem
        return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a workflow spec and generate an Airflow DAG.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--target", choices=sorted(RENDERERS), default="local")
    parser.add_argument("--out", type=Path, default=Path("dags"))
    args = parser.parse_args(argv)

    spec, errors = validate_yaml(args.spec.read_text())
    if errors:
        print(f"{args.spec}: {len(errors)} problem(s), no DAG generated:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    path = DagGenerator(RENDERERS[args.target]()).write(spec, args.out)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
