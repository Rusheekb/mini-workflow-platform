#!/usr/bin/env bash
# Airflow on a local Kubernetes cluster (kind), installed with the official Helm chart + KubernetesExecutor.
# Steps: generate Kubernetes-target DAGs -> build the task + Airflow images -> create the kind cluster ->
#        load images into it -> namespace + Secret -> helm install.
# Safe to re-run. Remove everything with scripts/k8s_down.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CLUSTER="mwp"
NAMESPACE="airflow"
TASK_IMAGE="mwp-tasks:0.1.0"
AIRFLOW_IMAGE="mwp-airflow:0.1.0"
CHART_VERSION="1.22.0"
PYTHON="${ROOT}/.venv/bin/python"

# One heavy thing at a time: the laptop can't run Airflow in Compose AND in kind.
if [ -n "$(docker compose -f deploy/docker-compose.yaml ps -q 2>/dev/null)" ]; then
  echo "Airflow is running in Docker Compose. Stop it first:"
  echo "  docker compose -f deploy/docker-compose.yaml down"
  exit 1
fi

echo "==> 1/6 Generating DAGs for the kubernetes target"
rm -rf build/k8s-dags && mkdir -p build/k8s-dags
for spec in specs/valid/*.yaml specs/demo/*.yaml; do
  "$PYTHON" -m backend.generator "$spec" --target kubernetes --out build/k8s-dags
done

echo "==> 2/6 Building images"
docker build -q -t "$TASK_IMAGE" tasks/
docker build -q -f deploy/airflow.Dockerfile -t "$AIRFLOW_IMAGE" .

echo "==> 3/6 Creating the kind cluster (if needed)"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config deploy/kind-config.yaml
fi
kubectl config use-context "kind-${CLUSTER}" >/dev/null

echo "==> 4/6 Loading images into the cluster (no registry needed locally)"
kind load docker-image "$TASK_IMAGE" "$AIRFLOW_IMAGE" --name "$CLUSTER"

echo "==> 5/6 Namespace + Secret for task pods"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
if [ -f .env ]; then
  ENV_TMP="$(mktemp)"
  chmod 600 "$ENV_TMP"
  trap 'rm -f "$ENV_TMP"' EXIT
  # Only what the task pods need; never the whole .env.
  grep -E '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_DEFAULT_REGION|MWP_S3_BUCKET|MWP_SECRET_NAME)=' .env \
    > "$ENV_TMP" || true
  kubectl create secret generic mwp-task-env -n "$NAMESPACE" --from-env-file="$ENV_TMP" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  echo "Secret mwp-task-env created from .env (values not shown)"
else
  echo "No .env: task pods will write to their own local disk, so multi-task DAGs can't pass data."
fi

echo "==> 6/6 Installing Airflow with Helm (first install takes several minutes)"
helm repo add apache-airflow https://airflow.apache.org >/dev/null 2>&1 || true
helm repo update apache-airflow >/dev/null
helm upgrade --install airflow apache-airflow/airflow --version "$CHART_VERSION" \
  --namespace "$NAMESPACE" -f deploy/helm-values.yaml --wait --timeout 15m
# Same image tag with new DAGs inside: restart so pods pick up the freshly loaded image.
kubectl rollout restart deployment -n "$NAMESPACE" >/dev/null
kubectl rollout status deployment -n "$NAMESPACE" --timeout 5m >/dev/null

echo
kubectl get pods -n "$NAMESPACE"
echo
echo "Airflow UI:  kubectl port-forward svc/airflow-api-server 8080:8080 -n ${NAMESPACE}"
echo "             then http://localhost:8080  (admin / admin)"
echo "Tear down:   scripts/k8s_down.sh"
