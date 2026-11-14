# Mini Workflow Platform

Teams describe a workflow in a YAML spec. The platform validates it, generates an Apache Airflow DAG,
and Airflow runs it — locally in Docker, then as Kubernetes pods.

    React UI -> FastAPI -> validate spec -> generate DAG file -> Airflow runs it

Full docs (architecture diagram, setup, AWS mapping) arrive in milestone M8.

## Quick start (dev)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt
```
