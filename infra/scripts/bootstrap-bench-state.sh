#!/usr/bin/env bash
# Bootstrap the Terraform state backend for infra/bench/.
#
# Creates the S3 bucket and DynamoDB lock table that hold Terraform state for
# the bench module. Run ONCE per AWS account, before the first `terraform
# init` in infra/bench/.
#
# Idempotent — re-running on an already-bootstrapped account is a no-op.
#
# Prereqs:
#   - AWS CLI configured with credentials that can create S3 + DynamoDB
#   - Bash 4+, jq optional
#
# Usage:
#   ./infra/scripts/bootstrap-bench-state.sh
#
# Override defaults via env vars:
#   AWS_REGION=us-west-2 ./infra/scripts/bootstrap-bench-state.sh
#   TF_STATE_BUCKET=my-tfstate TF_LOCK_TABLE=my-tflock ./infra/scripts/bootstrap-bench-state.sh
#
# After this completes, run:
#   cd infra/bench && terraform init && terraform plan

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
# S3 bucket names are global. Default suffixes the account ID so the name
# is unique across the world and obviously owned by your account. Override
# via env var if you need to bootstrap into a different account.
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${TF_STATE_BUCKET:-tracer-cloud-tfstate-${ACCOUNT_ID}}"
TABLE="${TF_LOCK_TABLE:-tracer-cloud-tflock}"

echo "Bootstrapping Terraform state backend"
echo "  Region:        ${REGION}"
echo "  State bucket:  ${BUCKET}"
echo "  Lock table:    ${TABLE}"
echo

# ---- S3 bucket -------------------------------------------------------------

if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "[s3]  bucket ${BUCKET} already exists — skipping create"
else
  echo "[s3]  creating bucket ${BUCKET}..."
  if [ "${REGION}" = "us-east-1" ]; then
    # us-east-1 is special: it does NOT accept LocationConstraint
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
  echo "[s3]  bucket created"
fi

echo "[s3]  enabling versioning..."
aws s3api put-bucket-versioning \
  --bucket "${BUCKET}" \
  --versioning-configuration Status=Enabled

echo "[s3]  enabling default encryption..."
aws s3api put-bucket-encryption \
  --bucket "${BUCKET}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

echo "[s3]  blocking all public access..."
aws s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# ---- DynamoDB lock table ---------------------------------------------------

if aws dynamodb describe-table --table-name "${TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  echo "[ddb] table ${TABLE} already exists — skipping create"
else
  echo "[ddb] creating lock table ${TABLE}..."
  aws dynamodb create-table \
    --table-name "${TABLE}" \
    --region "${REGION}" \
    --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --sse-specification Enabled=true >/dev/null

  echo "[ddb] waiting for table to become ACTIVE..."
  aws dynamodb wait table-exists --table-name "${TABLE}" --region "${REGION}"
  echo "[ddb] table is ACTIVE"
fi

echo
echo "Bootstrap complete."
echo "Next steps:"
echo "  cd infra/bench"
echo "  terraform init"
echo "  terraform plan"
