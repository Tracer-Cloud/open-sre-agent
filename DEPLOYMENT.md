## Deployment

OpenSRE has two primary AWS EC2 paths and a general hosted runtime option for
ASGI-compatible platforms:

- **Slack** — deployed and operated separately, not from this repo. The EC2
  path below never ships `SLACK_*` variables (Socket Mode is single-consumer —
  a second consumer would split events).
- **Telegram** — the EC2 gateway deploy below.

---

## Gateway Deploy — AMI + systemd (Telegram)

Runs the Telegram gateway directly on EC2 as a systemd service. The gateway is
baked into a custom AMI once; subsequent deploys launch from that AMI in ~2–3
minutes.

**Prerequisites:** AWS credentials with EC2 / IAM / SSM permissions. No Docker needed.

Copy [`.env.deploy.example`](.env.deploy.example) and export the required variables:

| Variable | Required | Used by |
| -------- | -------- | ------- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway service |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |
| `LLM_PROVIDER` + API key | Yes | Gateway service |

`SLACK_*` variables are ignored by the EC2 deploy (validation warns) — Slack is deployed and operated separately, not from this repo.

```bash
# Step 1 — bake a gateway AMI (run once per code change, takes ~5-10 minutes):
make bake-gateway

# Step 2 — launch EC2 instance from the saved AMI (fast):
make deploy-gateway

# Tear down (keeps AMI by default):
make destroy-gateway

# Full teardown including AMI deregistration:
OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1 make destroy-gateway
```

Rollback to a previously baked AMI:

```bash
OPENSRE_GATEWAY_AMI_ID=ami-<previous-id> make deploy-gateway
```

Check the running gateway via SSM:

```bash
aws ssm start-session --target <InstanceId>
# inside:
sudo systemctl status opensre-gateway
sudo journalctl -u opensre-gateway -f
```

Outputs are written to `~/.opensre/deployments/opensre-gateway.json`.

After deploy, the web API is reachable publicly:

```bash
curl http://<PublicIpAddress>:8000/health
```

Restrict the allowed source CIDR with `OPENSRE_WEB_API_INGRESS_CIDR` (default `0.0.0.0/0`).

### Direct deploy (no pre-baked AMI)

Installs OpenSRE inline on a fresh EC2 instance via SSM — slower but requires no bake step:

```bash
make deploy-gateway-direct
make destroy-gateway-direct
```

---

## Fargate multi-tenant deployment (Python CDK)

The shared ECS Fargate foundation is defined under
[`platform/deployment_fargate/fargate_fleet_infrastructure/`](platform/deployment_fargate/fargate_fleet_infrastructure/).
The IAM lifecycle Lambda and routes are Terraform-only: the
`modules/api_control_plane` module in the
[`opensre-infra/`](platform/deployment_fargate/opensre-infra/) submodule (see
the Terraform section below).
The bearer public-run Lambda, authorizer, and `/v1/runs` routes are Terraform
in `opensre-infra/modules/api_public_forwarder` (runtime under
[`opensre-infra/lambdas/api_public_forwarder/`](platform/deployment_fargate/opensre-infra/lambdas/api_public_forwarder/)).

Per-organization Gateway services, task definitions, tenant IAM roles, secrets, and
S3 Files access points are still created by the Python control-plane lifecycle
([`platform/deployment_fargate/opensre-infra/lambdas/api_control_plane/`](platform/deployment_fargate/opensre-infra/lambdas/api_control_plane/)).
The lifecycle also ensures one filesystem mount target per configured subnet and
reconciles the filesystem-wide tenant isolation policy.

### Prerequisites

1. Existing VPC and public subnet IDs for Gateway tasks. The MVP assigns public
   IPs so tasks have outbound access without provisioning NAT.
2. Existing S3 Files filesystem ID/ARN and client security group (from
   [opensre-infra-aws](https://github.com/Tracer-Cloud/opensre-infra-aws/tree/main)
   `memories` output, or supplied manually), ECR gateway image (digest-pinned),
   and credentials API URL.
3. A Secrets Manager secret containing the Postgres `DATABASE_URL`, plus the IAM
   role ARNs allowed to call lifecycle routes.
4. Docker for the Python 3.12 x86_64 Lambda bundles.
5. `uv sync --extra cdk` and AWS CDK CLI v2 (**2.1133.0+**; `npm install -g aws-cdk@2`).
6. One-time `cdk bootstrap` in the target account/region.
7. Before provisioning each tenant, its credentials bootstrap secret.

### Deploy

```bash
make cdk-synth
# Option A: resolve S3 Files params from the opensre-infra submodule
git submodule update --init platform/deployment_fargate/opensre-infra
cd platform/deployment_fargate/opensre-infra/stacks/shared && terraform init -input=false && cd -
./platform/deployment_fargate/scripts/cdk_deploy_fleet_from_infra_aws.sh \
  --environment dev \
  --parameters ...
# Option B: pass every fleet parameter explicitly
make cdk-deploy-fleet FLEET_CDK_ARGS='--parameters ...'
```

Control-plane and public-forwarder APIs are deployed with Terraform (see
below); before the control-plane's first deploy, apply the idempotent Postgres
schema by invoking
`platform/deployment_fargate/opensre-infra/lambdas/api_control_plane/migration_runtime.py`
out of band so run tables exist.

Verify fleet CDK without AWS credentials:

```bash
make cdk-verify
```

Tear down the fleet CDK stack:

```bash
make cdk-destroy
```

See the fleet README for parameter details. For the Lambda APIs, use the
Terraform section below.

### Terraform (opensre-infra submodule)

The platform is also defined as Terraform in the
[`opensre-infra/`](platform/deployment_fargate/opensre-infra/) submodule — and
for the control-plane API this is the only definition: `stacks/fargate`
composes the fleet foundation with `modules/api_control_plane` and
`modules/api_public_forwarder`, and reads S3 Files memories from
`stacks/shared` remote state (no bridge script needed). Requires Docker and
Terraform >= 1.5; no CDK bootstrap.

```bash
cd platform/deployment_fargate/opensre-infra
./scripts/build-lambda-bundles.sh --repo-root ../../..   # Lambda zips into dist/
cd stacks/fargate
cp terraform.tfvars.example terraform.tfvars             # fill in real values
terraform init -input=false && terraform apply
```

The Terraform module has no schema-migration step — the Postgres schema must
already exist (any prior control-plane deploy against the same database, or run
`platform/deployment_fargate/opensre-infra/lambdas/api_control_plane/migration_runtime.py`
once out of band). Verify a live
deployment end to end (provisions a gateway, prompts it through `/v1/runs`,
then stops and deletes it — the tenant bootstrap secret must exist first):

```bash
uv run python platform/deployment_fargate/scripts/e2e_fargate_verify.py \
  --control-plane-endpoint "$(terraform output -raw control_plane_api_endpoint)" \
  --public-forwarder-endpoint "$(terraform output -raw public_forwarder_api_endpoint)" \
  --organization-id org_tf_e2e \
  --lifecycle-role-arn arn:aws:iam::<account>:role/opensre-lifecycle-admin
```

---

## Runtime Environment (Hosted / General)

Deploy OpenSRE as a standard Python/FastAPI app using the repo `Dockerfile`, Railway,
ECS, Vercel, or another ASGI-capable host.

1. Build and deploy using your hosting provider's normal workflow.
2. Set `LLM_PROVIDER` and the matching provider API key:
    - `ANTHROPIC_API_KEY` when `LLM_PROVIDER=anthropic`
    - `OPENAI_API_KEY` when `LLM_PROVIDER=openai`
    - `OPENROUTER_API_KEY` when `LLM_PROVIDER=openrouter`
    - `GEMINI_API_KEY` when `LLM_PROVIDER=gemini`
3. Add `DATABASE_URI` and `REDIS_URI` for hosted layouts that need persistence.
4. Add any additional environment variables required by your integrations.

Minimum environment:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

The full set of supported provider keys and optional model overrides is documented in
[`.env.example`](.env.example).

### Railway

Ensure the Railway project has Postgres and Redis services, and that the OpenSRE service
has `DATABASE_URI` and `REDIS_URI` set to those connection strings before deploying.

For telemetry labeling, set `OPENSRE_DEPLOYMENT_METHOD=railway` on the Railway service.

---
