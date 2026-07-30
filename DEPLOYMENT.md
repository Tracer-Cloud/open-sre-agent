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
make build-gateway-image

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
make install-gateway-on-new-server
make destroy-gateway-on-new-server
```

---

## Fargate multi-tenant deployment (Terraform)

The shared ECS Fargate foundation, IAM lifecycle API, and public-run API live in
the private [`Tracer-Cloud/opensre-infra-aws`](https://github.com/Tracer-Cloud/opensre-infra-aws)
repository, vendored here as a git submodule at
[`platform/deployment_multi_tenant/`](platform/deployment_multi_tenant/).

Internal developers (with access to that private repo) must initialize it before
deploying or running Fargate/control-plane tests:

```bash
git submodule update --init platform/deployment_multi_tenant
```

Contents once checked out:

- Fleet + APIs: [`modules/fargate_fleet`](platform/deployment_multi_tenant/modules/fargate_fleet/)
  (composes [`modules/api_control_plane`](platform/deployment_multi_tenant/modules/api_control_plane/)
  and [`modules/api_public_forwarder`](platform/deployment_multi_tenant/modules/api_public_forwarder/))
- Control-plane runtime:
  [`lambda_control_plane/`](platform/deployment_multi_tenant/lambda_control_plane/)
- Public-forwarder runtime:
  [`lambda_public_forwarder/`](platform/deployment_multi_tenant/lambda_public_forwarder/)
- Shared S3 Files memories: [`stacks/shared`](platform/deployment_multi_tenant/stacks/shared/)

Per-organization Gateway services, task definitions, tenant IAM roles, secrets, and
S3 Files access points are created by the Python control-plane lifecycle. The
lifecycle also ensures one filesystem mount target per configured subnet and
reconciles the filesystem-wide tenant isolation policy.

### Prerequisites

1. Existing VPC / subnet placement for Gateway tasks (the fleet module uses the
   default VPC and memories mount subnets from `stacks/shared`).
2. Shared S3 Files filesystem applied via `stacks/shared`, ECR gateway image
   (digest-pinned), and credentials API URL.
3. A Secrets Manager secret containing the Postgres `DATABASE_URL`, plus the IAM
   role ARNs allowed to call lifecycle routes.
4. Docker for the Python 3.12 x86_64 Lambda bundles.
5. Terraform >= 1.5.
6. Before provisioning each tenant, its credentials bootstrap secret.

### Deploy

```bash
cd platform/deployment_multi_tenant/stacks/shared && terraform init -input=false && cd -
cd platform/deployment_multi_tenant
./scripts/build-lambda-bundles.sh --repo-root ../..   # Lambda zips into dist/
cd modules/fargate_fleet
cp terraform.tfvars.example terraform.tfvars          # fill in real values
terraform init -input=false && terraform apply
```

Before the control-plane's first deploy, apply the idempotent Postgres schema by
invoking
`platform/deployment_multi_tenant/lambda_control_plane/migration_runtime.py`
out of band so run tables exist.

Bundle whitelist check (no AWS credentials):

```bash
make cdk-verify
```

Verify a live deployment end to end (provisions a gateway, prompts it through
`/v1/runs`, then stops and deletes it — the tenant bootstrap secret must exist
first):

```bash
uv run python platform/deployment_multi_tenant/scripts/e2e_fargate_verify.py \
  --control-plane-endpoint "$(terraform output -raw control_plane_api_endpoint)" \
  --public-forwarder-endpoint "$(terraform output -raw public_forwarder_api_endpoint)" \
  --organization-id org_tf_e2e \
  --lifecycle-role-arn arn:aws:iam::<account>:role/opensre-lifecycle-admin
```

See [platform/deployment_multi_tenant/TERRAFORM.md](platform/deployment_multi_tenant/TERRAFORM.md)
and [platform/deployment_multi_tenant/README.md](platform/deployment_multi_tenant/README.md)
for stack layout and naming.

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
