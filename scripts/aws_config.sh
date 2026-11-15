# Shared names for setup_aws.sh and teardown_aws.sh. Sourced, not run directly.
# The AWS "project" account only allows regional services (S3, Secrets Manager, ECR) in its selected
# region, which is us-east-2. Override with: AWS_PROJECT_REGION=... ./scripts/setup_aws.sh
REGION="${AWS_PROJECT_REGION:-us-east-2}"
TAG_KEY="project"
TAG_VALUE="mini-workflow-platform"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="mwp-data-${ACCOUNT_ID}"            # bucket names are global, so include the account id
SECRET_NAME="mwp/vendor-api-key"
REPO="mwp-tasks"
IAM_USER="mwp-airflow-tasks"
POLICY_NAME="mwp-least-privilege"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env"

# Set KEY=VALUE in .env (replace if present, append if not). The file is gitignored and chmod 600.
set_env() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" || true
  echo "${key}=${value}" >> "${ENV_FILE}.tmp"
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}
