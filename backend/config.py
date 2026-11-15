"""Settings for the API, read from environment variables (and .env for local dev)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    dags_dir: Path
    specs_dir: Path
    default_target: str
    airflow_url: str
    airflow_username: str
    airflow_password: str = field(repr=False)  # repr=False: never shows up if settings get logged/printed

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")  # no-op if the file doesn't exist; never overrides real env vars
        return cls(
            dags_dir=Path(os.environ.get("MWP_DAGS_DIR", ROOT / "dags")),
            specs_dir=Path(os.environ.get("MWP_SPECS_DIR", ROOT / "specs" / "submitted")),
            default_target=os.environ.get("MWP_TARGET", "local"),
            airflow_url=os.environ.get("AIRFLOW_API_URL", "http://localhost:8080"),
            airflow_username=os.environ.get("AIRFLOW_USERNAME", "airflow"),
            airflow_password=os.environ.get("AIRFLOW_PASSWORD", "airflow"),
        )
