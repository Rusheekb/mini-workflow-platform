# Custom Airflow image with our DAGs baked in (no git-sync or shared volume needed on Kubernetes).
# Built by scripts/k8s_up.sh from the repo root:
#   docker build -f deploy/airflow.Dockerfile -t mwp-airflow:0.1.0 .
FROM apache/airflow:3.3.2-python3.12

# DAGs generated with --target kubernetes into build/k8s-dags/ by the build script.
COPY --chown=airflow:root build/k8s-dags/ /opt/airflow/dags/
