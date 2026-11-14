"""Shared pytest setup.

Point Airflow at a throwaway home folder BEFORE anything imports airflow, so tests never touch
~/airflow and never load Airflow's example DAGs.
"""

import os
import tempfile

os.environ.setdefault("AIRFLOW_HOME", tempfile.mkdtemp(prefix="mwp-airflow-home-"))
os.environ.setdefault("AIRFLOW__CORE__LOAD_EXAMPLES", "False")
