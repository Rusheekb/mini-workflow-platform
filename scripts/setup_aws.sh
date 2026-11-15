#!/usr/bin/env bash
# Creates the project's AWS resources in its region (us-east-2), all tagged project=mini-workflow-platform:
#   S3 bucket (private, encrypted, objects expire after 30 days), Secrets Manager secret,
#   ECR repository (immutable tags, scan on push), and an IAM user limited to exactly those three.
# Safe to re-run: existing resources are left alone.
# Cost if torn down within a few days: well under $0.10 (the secret is $0.40/month, prorated).
# Undo everything with: scripts/teardown_aws.sh
set -euo pipefail
source "$(dirname "$0")/aws_config.sh"
TAGS_KV="Key=${TAG_KEY},Value=${TAG_VALUE}"

echo "Account ${ACCOUNT_ID}, region ${REGION}"

# Some accounts (e.g. AWS free-plan projects) block services with an organization policy (SCP).
# Detect that up front and skip those services instead of failing halfway through.
blocked_by_org() {
  local out
  out="$(aws "$@" 2>&1)" || true   # capture first: with `pipefail`, piping a failing aws call into grep misreports
  [[ "$out" == *"explicit deny"* ]]
}
HAVE_SECRET=1
HAVE_ECR=1
if blocked_by_org secretsmanager list-secrets --region "$REGION" --max-results 1; then
  HAVE_SECRET=0
  echo "NOTE: Secrets Manager is blocked in this account -> skipping the secret"
fi
if blocked_by_org ecr describe-repositories --region "$REGION" --max-items 1; then
  HAVE_ECR=0
  echo "NOTE: ECR is blocked in this account -> skipping the image repository (M7 loads images into kind directly)"
fi

# --- S3 -----------------------------------------------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "S3 bucket ${BUCKET} already exists"
else
  if [ "$REGION" = "us-east-1" ]; then   # us-east-1 is the one region that rejects a LocationConstraint
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
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
if [ "$HAVE_SECRET" = "0" ]; then
  :
elif aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "secret ${SECRET_NAME} already exists"
else
  # A random placeholder standing in for a vendor's API key. Never echoed.
  aws secretsmanager create-secret --region "$REGION" --name "$SECRET_NAME" \
    --description "Placeholder vendor API key for mini-workflow-platform" \
    --secret-string "demo-$(openssl rand -hex 16)" --tags "$TAGS_KV" >/dev/null
  echo "created secret ${SECRET_NAME}"
fi

# --- ECR ----------------------------------------------------------------------------------------
if [ "$HAVE_ECR" = "1" ]; then
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
fi

# --- IAM user with a least-privilege inline policy ---------------------------------------------
if aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  echo "IAM user ${IAM_USER} already exists"
else
  aws iam create-user --user-name "$IAM_USER" --tags "$TAGS_KV" >/dev/null
  echo "created IAM user ${IAM_USER}"
fi
POLICY_FILE="$(mktemp)"
# Fill in the placeholders, and drop statements for services we skipped: least privilege means not
# granting access to things that don't exist.
python3 - "${ROOT}/deploy/aws/iam-policy.json" "$POLICY_FILE" "$HAVE_SECRET" "$HAVE_ECR" <<PY
import json, sys
src, dst, have_secret, have_ecr = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4] == "1"
text = open(src).read()
for k, v in {"__BUCKET__": "${BUCKET}", "__REGION__": "${REGION}", "__ACCOUNT_ID__": "${ACCOUNT_ID}",
             "__SECRET_NAME__": "${SECRET_NAME}", "__REPO__": "${REPO}"}.items():
    text = text.replace(k, v)
policy = json.loads(text)
skip = set()
if not have_secret:
    skip.add("ReadOnlyOurSecret")
if not have_ecr:
    skip |= {"EcrLoginHasNoResourceScope", "PushPullOnlyOurRepo"}
policy["Statement"] = [s for s in policy["Statement"] if s["Sid"] not in skip]
json.dump(policy, open(dst, "w"), indent=2)
print("policy grants:", ", ".join(s["Sid"] for s in policy["Statement"]))
PY
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
if [ "$HAVE_SECRET" = "1" ]; then
  set_env MWP_SECRET_NAME "$SECRET_NAME"
else
  set_env MWP_SECRET_NAME replace-me   # tasks skip the secret step
fi

echo
echo "Done. Restart Airflow so the containers pick up .env:"
echo "  docker compose -f deploy/docker-compose.yaml up -d --force-recreate"
echo "REMEMBER: run scripts/teardown_aws.sh when you're finished."
