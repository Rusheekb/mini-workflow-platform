"""The workflow spec format (our "common specification model"), as Pydantic models.

These models check SHAPE only: required fields exist, types are right, no unknown fields.
Rules that need to look across fields (duplicate IDs, cycles, valid cron, pinned images, ...)
live in validator.py.
"""

from pydantic import BaseModel, ConfigDict, Field

# Task IDs and workflow names become Python variable names / Airflow dag_ids in the generated DAG,
# so they must be valid identifiers: letters, digits, underscores, not starting with a digit.
IDENTIFIER = r"^[A-Za-z_][A-Za-z0-9_]*$"


class TaskSpec(BaseModel):
    """One step in a workflow. Runs `command` inside container `image`."""

    # extra="forbid": unknown keys are errors. Catches typos like `depend_on:` that would
    # otherwise be silently ignored (and the task would run with no dependencies!).
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=IDENTIFIER, max_length=100)
    image: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    memory_limit: str = "256Mi"
    depends_on: list[str] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    """A whole workflow: metadata plus an ordered list of tasks."""

    model_config = ConfigDict(extra="forbid")

    workflow_name: str = Field(pattern=IDENTIFIER, max_length=200)
    schedule: str
    owner: str = Field(min_length=1)
    retries: int = Field(default=0, ge=0, le=10)
    retry_delay_minutes: int = Field(default=5, ge=1, le=120)
    tags: list[str] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(min_length=1)
