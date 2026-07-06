## Deployment

OpenSRE has two primary deployment paths (both target AWS EC2) and a general hosted
runtime option for ASGI-compatible platforms.

---

## EC2 Deploy — Docker/ECR (web + gateway)

Runs `opensre-web` and `opensre-gateway` as Docker containers on a single EC2 instance.
The image is built once and pushed to ECR; subsequent redeploys reuse the cached image.

**Prerequisites:** Docker daemon running locally, AWS credentials with EC2 / ECR / IAM /
SSM permissions, region defaults to `us-east-1`.

Copy [`.env.deploy.example`](.env.deploy.example) and export the required variables:

| Variable | Required | Used by |
| -------- | -------- | ------- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway container |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |
| `LLM_PROVIDER` + API key | Yes | Both containers |

```bash
# Step 1 — build and push Docker image to ECR (run once per code change):
make build-image

# Step 2 — launch EC2 instance using the pre-built image (fast, no Docker build):
make deploy

# Tear down the stack (keeps ECR image by default):
make destroy

# Full teardown including ECR repository:
OPENSRE_DESTROY_PURGE_ECR=1 make destroy
```

After deploy:

```bash
curl http://<PublicIpAddress>:8000/health
```

Outputs (instance ID, public IP, image URI) are written to
`~/.opensre/deployments/opensre-ec2.json`.

`make deploy` auto-destroys any existing stack before provisioning a fresh one. Set
`OPENSRE_DEPLOY_ABORT_IF_EXISTS=1` to fail instead of auto-destroying.

---

## Gateway Deploy — AMI + systemd (Telegram gateway only)

Runs the Telegram gateway directly on EC2 as a systemd service — no Docker or ECR
required. The gateway is baked into a custom AMI once; subsequent deploys launch from
that AMI in ~2–3 minutes.

**Prerequisites:** AWS credentials with EC2 / IAM / SSM permissions. No Docker needed.

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

### Direct deploy (no pre-baked AMI)

Installs OpenSRE inline on a fresh EC2 instance via SSM — slower but requires no bake step:

```bash
make deploy-gateway-direct
make destroy-gateway-direct
```

---

## Comparison

|  | Docker/ECR (`make deploy`) | Gateway (`make deploy-gateway`) |
| - | - | - |
| What deploys | web + gateway containers | gateway service only |
| Runtime | Docker inside EC2 | systemd on EC2 host |
| Shell access | Inside slim container | Full EC2 host |
| ECR repository | Required | Not needed |
| Update path | `make build-image && make deploy` | `make bake-gateway && make deploy-gateway` |

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
