#!/usr/bin/env bash
# Creates the project's AWS resources in us-east-1, all tagged project=mini-workflow-platform:
#   S3 bucket (private, encrypted, objects expire after 30 days), Secrets Manager secret,
#   ECR repository (immutable tags, scan on push), and an IAM user limited to exactly those three.
# Safe to re-run: existing resources are left alone.
# Cost if torn down within a few days: well under $0.10 (the secret is $0.40/month, prorated).
# Undo everything with: scripts/teardown_aws.sh
set -euo pipefail
source "$(dirname "$0")/aws_config.sh"
TAGS_KV="Key=${TAG_KEY},Value=${TAG_VALUE}"

echo "Account ${ACCOUNT_ID}, region ${REGION}"

# --- S3 -----------------------------------------------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "S3 bucket ${BUCKET} already exists"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null   # us-east-1 needs no LocationConstraint
  echo "created S3 bucket ${BUCKET}"
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging "TagSet=[{${TAGS_KV}}]"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-after-30-days","Status":"Enabled","Filter":{},"Expiration":{"Days":30}}]}'

# --- Secrets Manager ----------------------------------------------------------------------------
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "secret ${SECRET_NAME} already exists"
else
  # A random placeholder standing in for a vendor's API key. Never echoed.
  aws secretsmanager create-secret --region "$REGION" --name "$SECRET_NAME" \
    --description "Placeholder vendor API key for mini-workflow-platform" \
    --secret-string "demo-$(openssl rand -hex 16)" --tags "$TAGS_KV" >/dev/null
  echo "created secret ${SECRET_NAME}"
fi

# --- ECR ----------------------------------------------------------------------------------------
if aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1; then
  echo "ECR repository ${REPO} already exists"
else
  # IMMUTABLE: a tag like 0.1.0 can never be overwritten -- the registry enforces our "pinned tag" rule.
  aws ecr create-repository --region "$REGION" --repository-name "$REPO" \
    --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true \
    --tags "$TAGS_KV" >/dev/null
  echo "created ECR repository ${REPO}"
fi
aws ecr put-lifecycle-policy --region "$REGION" --repository-name "$REPO" --lifecycle-policy-text \
  '{"rules":[{"rulePriority":1,"description":"keep last 3 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}' >/dev/null

# --- IAM user with a least-privilege inline policy ---------------------------------------------
if aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  echo "IAM user ${IAM_USER} already exists"
else
  aws iam create-user --user-name "$IAM_USER" --tags "$TAGS_KV" >/dev/null
  echo "created IAM user ${IAM_USER}"
fi
POLICY_FILE="$(mktemp)"
sed -e "s|__BUCKET__|${BUCKET}|g" -e "s|__REGION__|${REGION}|g" -e "s|__ACCOUNT_ID__|${ACCOUNT_ID}|g" \
    -e "s|__SECRET_NAME__|${SECRET_NAME}|g" -e "s|__REPO__|${REPO}|g" \
    "${ROOT}/deploy/aws/iam-policy.json" > "$POLICY_FILE"
aws iam put-user-policy --user-name "$IAM_USER" --policy-name "$POLICY_NAME" \
  --policy-document "file://${POLICY_FILE}"
rm -f "$POLICY_FILE"
echo "attached policy ${POLICY_NAME}"

# --- Access key -> .env (only if the user has none yet) ------------------------------------------
[ -f "$ENV_FILE" ] || cp "${ROOT}/.env.example" "$ENV_FILE"
KEY_COUNT="$(aws iam list-access-keys --user-name "$IAM_USER" --query 'length(AccessKeyMetadata)' --output text)"
if [ "$KEY_COUNT" = "0" ]; then
  read -r KEY_ID KEY_SECRET < <(aws iam create-access-key --user-name "$IAM_USER" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
  set_env AWS_ACCESS_KEY_ID "$KEY_ID"
  set_env AWS_SECRET_ACCESS_KEY "$KEY_SECRET"
  echo "created access key ${KEY_ID} and saved it to .env (secret not shown)"
else
  echo "IAM user already has an access key; .env left as is"
fi
set_env AWS_REGION "$REGION"
set_env AWS_DEFAULT_REGION "$REGION"
set_env MWP_S3_BUCKET "$BUCKET"
set_env MWP_SECRET_NAME "$SECRET_NAME"

echo
echo "Done. Restart Airflow so the containers pick up .env:"
echo "  docker compose -f deploy/docker-compose.yaml up -d --force-recreate"
echo "REMEMBER: run scripts/teardown_aws.sh when you're finished."
