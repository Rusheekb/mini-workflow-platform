#!/usr/bin/env bash
# Deletes everything setup_aws.sh created. Safe to re-run: missing resources are skipped.
set -uo pipefail   # no -e: keep going if one resource is already gone
source "$(dirname "$0")/aws_config.sh"

echo "Tearing down mini-workflow-platform resources in ${ACCOUNT_ID} / ${REGION}"

# IAM: keys and inline policy must go before the user can be deleted.
if aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  for key in $(aws iam list-access-keys --user-name "$IAM_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    aws iam delete-access-key --user-name "$IAM_USER" --access-key-id "$key" && echo "deleted access key ${key}"
  done
  aws iam delete-user-policy --user-name "$IAM_USER" --policy-name "$POLICY_NAME" 2>/dev/null
  aws iam delete-user --user-name "$IAM_USER" && echo "deleted IAM user ${IAM_USER}"
else
  echo "IAM user ${IAM_USER} not found"
fi

# S3: a bucket must be empty before it can be deleted.
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3 rm "s3://${BUCKET}" --recursive --quiet
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" && echo "deleted S3 bucket ${BUCKET}"
else
  echo "S3 bucket ${BUCKET} not found"
fi

# Secrets Manager: skip the 7-30 day recovery window so nothing lingers.
aws secretsmanager delete-secret --region "$REGION" --secret-id "$SECRET_NAME" \
  --force-delete-without-recovery >/dev/null 2>&1 && echo "deleted secret ${SECRET_NAME}" \
  || echo "secret ${SECRET_NAME} not found"

# ECR: --force deletes the images too.
aws ecr delete-repository --region "$REGION" --repository-name "$REPO" --force >/dev/null 2>&1 \
  && echo "deleted ECR repository ${REPO}" || echo "ECR repository ${REPO} not found"

# Point the tasks back at local storage and drop the (now useless) keys from .env.
if [ -f "$ENV_FILE" ]; then
  set_env AWS_ACCESS_KEY_ID replace-me
  set_env AWS_SECRET_ACCESS_KEY replace-me
  set_env MWP_S3_BUCKET replace-me
  echo "reset AWS values in .env"
fi

echo
echo "Check nothing is left (each should say 'not found' / NoSuchEntity / 404):"
echo "  aws s3api head-bucket --bucket ${BUCKET}"
echo "  aws iam get-user --user-name ${IAM_USER}"
