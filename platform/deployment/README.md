# `platform/deployment/`

AWS EC2 deployment and shared provisioning primitives for OpenSRE.

## What's here

| Path | Purpose |
| --- | --- |
| [`aws/`](aws/) | Shared AWS SDK primitives (`client`, `config`, VPC/SG, EC2/IAM, ECR, SSM). |
| [`ecr_deploy/`](ecr_deploy/) | Docker/ECR EC2 provisioning: `opensre-web` + `opensre-gateway` on one instance. |
| [`fargate/`](fargate/) | ECS Fargate + RDS layout (plan/dry-run; apply not implemented yet). |
| [`gateway/`](gateway/) | AMI + systemd deployment path for the Telegram gateway only (no Docker/ECR). See [gateway/README.md](gateway/README.md). |
| `install-proxy/` | Install proxy utility (Cloudflare Worker). |

## EC2 deploy commands

Run from the **repo root**. Requires `make install` first.

| Command | What it does |
| --- | --- |
| `make deploy` | Destroy any existing stack, then build image → push ECR → launch EC2 → wait for health |
| `make destroy` | Terminate instance, delete security group + IAM profile/role, remove local outputs |
| `make test-deploy` | Run `tests/deployment/ec2/` e2e tests (live AWS; skipped in CI) |

Equivalent Python entrypoints:

```bash
uv run python -m platform.deployment.ecr_deploy.lifecycle deploy
uv run python -m platform.deployment.ecr_deploy.lifecycle destroy
```

### Prerequisites

1. **Docker** — daemon running locally (`docker build` runs on your machine).
2. **AWS credentials** — static keys or role via the default boto3 chain (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, or `AWS_ROLE_ARN`).
3. **Permissions** — EC2, ECR, IAM, VPC, and SSM for the deploy account/region.
4. **Region** — hardcoded to `us-east-1` in [`aws/config.py`](aws/config.py).

### Environment

`make deploy` validates required variables **before** cleanup or provisioning and prints
any missing keys (with `MISSING:` / `WARN:` labels).

Copy [`.env.deploy.example`](../../.env.deploy.example) to `.env` in the repo root (or export vars):

| Variable | Required | Used by |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway container |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |
| `LLM_PROVIDER` + API key | Yes | Both containers |
| `EC2_KEY_NAME` | No | Optional SSH debug key pair |

### What `make deploy` creates

One stack named `opensre-ec2`:

- **ECR** repository `opensre` (image built from root `Dockerfile`)
- **EC2** `t2.micro` in the account default VPC (public subnet)
- **Security group** — inbound TCP 8000 (web); gateway uses outbound-only polling
- **IAM** instance profile — ECR pull, SSM, Bedrock (if used)
- **Containers on the instance:**
  - `opensre-web` — `MODE=web`, port `8000`
  - `opensre-gateway` — `MODE=gateway`, Telegram long-polling

Outputs are written to `~/.opensre/deployments/opensre-ec2.json` (`InstanceId`, `PublicIpAddress`, `ImageUri`, etc.).

After deploy:

```bash
curl http://<PublicIpAddress>:8000/health
```

### Redeploy behavior

`make deploy` checks for an existing stack before provisioning:

- If `~/.opensre/deployments/opensre-ec2.json` exists **or** active EC2 instances are tagged with `tracer:stack=opensre-ec2`, deploy **auto-destroys** the previous stack (with a console warning) and then provisions a fresh one.
- This prevents orphan instances when deploy is run twice without an explicit `make destroy`.
- Set `OPENSRE_DEPLOY_ABORT_IF_EXISTS=1` to fail instead of auto-destroying (useful when you want deploy to be strictly manual).

### What `make destroy` removes

- EC2 instance (from outputs file)
- Security group
- IAM instance profile and role
- ECR repository `opensre` (and pushed images)

### E2E test infrastructure (separate from `make deploy`)

These Makefile targets provision **test-case** AWS stacks for the e2e suite, not the OpenSRE runtime:

| Command | Stack |
| --- | --- |
| `make deploy-lambda` / `make destroy-lambda` | Lambda test fixture |
| `make deploy-prefect` / `make destroy-prefect` | Prefect ECS Fargate fixture |
| `make deploy-flink` / `make destroy-flink` | Flink ECS fixture |

## Cloud-OpsBench AWS infrastructure

The Terraform module for running Cloud-OpsBench on AWS Fargate lives with the
benchmark code at
[`tests/benchmarks/cloudopsbench/infra/`](../../tests/benchmarks/cloudopsbench/infra/).
The one-time Terraform state bootstrap script lives at
[`tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh`](../../tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh).
See that directory's [README](../../tests/benchmarks/cloudopsbench/infra/README.md)
and the benchmark runner guide at
[`tests/benchmarks/cloudopsbench/README.md`](../../tests/benchmarks/cloudopsbench/README.md).
