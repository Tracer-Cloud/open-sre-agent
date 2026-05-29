# `infra/bench/` — Cloud-OpsBench AWS environment

Terraform module that provisions the AWS resources for running Cloud-OpsBench
on Fargate. Owned by the benchmark workstream (see project issue for context).

## What this provisions

- **ECR repo** for the bench container image
- **Fargate cluster + task definition** (CPU/memory parameterized; default 4 vCPU / 8 GiB)
- **IAM**: task role (container runtime), execution role (ECS), and two GitHub Actions OIDC roles — `github-actions` (RunTask + Seed) and `terraform-plan` (ReadOnlyAccess + state bucket read). No long-lived AWS access keys required.
- **S3 bucket** for per-run artifacts (`runs/<date>-<sha>/`), versioned + encrypted
- **Secrets Manager** entries: `anthropic_api_key`, `openai_api_key`, `deepseek_api_key`, `hf_token` (values seeded out-of-band)
- **CloudWatch log group** for task logs (30-day retention by default)

**Grafana Cloud:** if/when the team wants to view bench logs in the Grafana UI, set up Grafana Cloud's native CloudWatch data source — configured in Grafana's web UI, no Terraform change. Logs live in CloudWatch; Grafana queries them.

What this does **not** provision: container image, the GitHub workflow that
launches tasks, or any framework `.py` source. Those land separately when the
bench framework is back on the active branch.

## Prerequisites

- AWS CLI configured with credentials that have permission to create IAM, ECS,
  ECR, S3, Secrets Manager, CloudWatch resources
- `terraform` ≥ 1.7 (see `.terraform-version` — `tfenv install` if available)
- A pre-existing S3 bucket + DynamoDB table for the Terraform state backend
  (see "First apply" below)

## First apply

The S3 backend bucket + DynamoDB lock table can't be created by this module
itself (chicken-and-egg). Use the bootstrap script — it's idempotent so safe
to re-run:

```bash
./infra/scripts/bootstrap-bench-state.sh
```

The script creates:

- S3 bucket `tracer-cloud-tfstate-<account-id>` — versioned, AES256-encrypted, public access blocked. Suffixed with account ID because S3 bucket names are global.
- DynamoDB table `tracer-cloud-tflock` — partition key `LockID` (String), PAY_PER_REQUEST, SSE enabled

Both in `us-east-1` by default. Override with env vars:

```bash
AWS_REGION=us-west-2 TF_STATE_BUCKET=my-tfstate ./infra/scripts/bootstrap-bench-state.sh
```

Once bootstrap completes:

```bash
cd infra/bench
terraform init
terraform plan
terraform apply
```

If you bootstrap in a non-default region or with non-default names, update
the matching values in `backend.tf` before `terraform init`.

## Seeding secret values

Values are NOT stored in Terraform. After `terraform apply`:

Two options — pick whichever matches your workflow.

**Option 1 — via the seeding workflow (recommended).** Keeps the keys off your laptop entirely. Add `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `HF_TOKEN` as GitHub repo secrets (Settings → Secrets and variables → Actions), then trigger `.github/workflows/bench-seed-secret.yml` once per target — pick the secret from the dropdown:

```bash
gh workflow run bench-seed-secret.yml -f secret=anthropic_api_key
gh workflow run bench-seed-secret.yml -f secret=openai_api_key
gh workflow run bench-seed-secret.yml -f secret=deepseek_api_key
gh workflow run bench-seed-secret.yml -f secret=hf_token
```

**Option 2 — direct AWS CLI from your laptop.** Faster for a one-off bootstrap, less centralized for rotation:

```bash
aws secretsmanager put-secret-value \
  --secret-id opensre-bench/llm/anthropic_api_key \
  --secret-string "$ANTHROPIC_API_KEY"

aws secretsmanager put-secret-value \
  --secret-id opensre-bench/llm/openai_api_key \
  --secret-string "$OPENAI_API_KEY"

aws secretsmanager put-secret-value \
  --secret-id opensre-bench/llm/deepseek_api_key \
  --secret-string "$DEEPSEEK_API_KEY"

aws secretsmanager put-secret-value \
  --secret-id opensre-bench/llm/hf_token \
  --secret-string "$HF_TOKEN"
```

> DeepSeek uses an OpenAI-compatible API. The base URL (`https://api.deepseek.com`)
> is non-secret — set as a non-secret task env var in `fargate.tf`.

## Viewing logs in Grafana Cloud (optional)

Bench logs go to CloudWatch. If you want to query them through the Grafana
UI (because the team's other dashboards live there), set up Grafana Cloud's
native CloudWatch data source — this is a click-through setup in Grafana
Cloud's web UI, not a Terraform change:

1. Grafana Cloud → Connections → Add new connection → Amazon CloudWatch
2. Configure with an AWS access key/role that has `logs:GetLogEvents`,
   `logs:DescribeLogGroups`, `logs:DescribeLogStreams` on
   `/ecs/opensre-bench`
3. Create a dashboard or use Grafana Explore against the new data source

Logs continue to live in CloudWatch; Grafana just queries them. Zero infra
change on the AWS side. Faster to set up than shipping logs to Loki, and
keeps the bench infra minimal.

Get secret ARNs with `terraform output secret_arns` if you prefer ARN-based
addressing.

## Launching a bench task (manual / debug)

After image is pushed to ECR and you want to launch the task by hand
(GitHub workflow will do this automatically once it lands):

```bash
CLUSTER=$(terraform output -raw ecs_cluster_name)
TASK_DEF=$(terraform output -raw task_definition_family)
SUBNETS=$(terraform output -json subnet_ids | jq -r 'join(",")')
SG=$(terraform output -raw security_group_id)

aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}"
```

Live logs: `aws logs tail /ecs/opensre-bench --follow` (or via the AWS Console).

## Building and pushing the bench container image

`infra/bench/Dockerfile.bench` builds the bench container (context is the repo root).

```bash
# From repo root
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URL=$(cd infra/bench && terraform output -raw ecr_repository_url)
TAG=$(git rev-parse --short HEAD)         # or any unique value

# Login to ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com"

# Build (linux/amd64 — Fargate runs amd64 unless you opt into ARM)
docker buildx build \
  --platform linux/amd64 \
  -f infra/bench/Dockerfile.bench \
  -t "$ECR_URL:$TAG" \
  --load .

# Push
docker push "$ECR_URL:$TAG"

# Apply Terraform with the new tag
cd infra/bench
terraform apply -var="image_tag=$TAG"
```

The image references `tests.benchmarks._framework.cli` as its entrypoint —
that module lives on the bench framework feature branch. The image will
**build** cleanly today (the `tests/benchmarks/` directory exists on main)
but the runtime entrypoint requires the framework branch merged. Sequencing:

1. Merge bench framework feature branch → main
2. Build and push the image (above)
3. `terraform apply -var=image_tag=<TAG>` to point the task definition at it
4. `aws ecs run-task` to launch the bench

## Setting the container image tag

ECR is configured with `IMMUTABLE` tag mutability — a tag can be pushed
exactly once; subsequent pushes of the same tag fail. So **every image
build must use a unique tag** (semver, git SHA, or build ID), and the
Terraform apply explicitly chooses which tag to deploy.

The placeholder default `bootstrap` lets `terraform apply` succeed before
any image exists. Once you have a real image:

```bash
# One-shot
terraform apply -var=image_tag=v1.0.0

# Or pin in terraform.tfvars (NOT committed)
echo 'image_tag = "v1.0.0"' >> terraform.tfvars
terraform apply
```

## Pre-registration pinning

For a publication-grade bench run, pin in the pre-registration YAML:

- `task_definition_arn` from `terraform output -raw task_definition_arn`
  (includes the revision number — different from `task_definition_family`)
- `image_tag` value used at apply time (or, better, the resolved image
  digest from `docker inspect`/`aws ecr describe-images`)
- AWS region from `var.region`
- `task_cpu` and `task_memory` from the apply'd values

This lets a reviewer reproduce the exact compute environment used for a
published study.

## Destroying

```bash
terraform destroy
```

Note: Secrets Manager has a 30-day recovery window — a destroyed secret can
be recovered within 30 days via `aws secretsmanager restore-secret`. To
force immediate deletion (e.g., in a throwaway dev account), set
`recovery_window_in_days = 0` in `secrets.tf` before destroying.

## Cost expectations

- Idle (no bench running): ~$1/month (Secrets Manager $0.40 × 3, ECR storage trivial, log group empty)
- Per-run (multi-day Fargate): ~$50-150 depending on duration
- S3 storage: ~$0.025/GB/month for results, growth is slow

## Future work (intentionally out of scope here)

- VPC dedicated to bench (current uses default VPC)
- ECS Service for long-running benchmark watcher (current is one-off tasks)
- Cross-region replication of the results bucket
- KMS customer-managed keys (current uses AWS-managed AES256)
