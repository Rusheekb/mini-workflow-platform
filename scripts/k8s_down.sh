#!/usr/bin/env bash
# Deletes the kind cluster (and everything in it: Airflow, its database, pods, the Secret).
set -euo pipefail
kind delete cluster --name mwp
