# `platform/deployment/gateway/`

AMI + systemd deployment path for the OpenSRE Telegram gateway.

This path is an alternative to the main Docker/ECR web+gateway deploy.
It runs the gateway process directly on the EC2 host as a systemd service,
so shell commands (like `curl`, `systemctl`, `sudo`) work normally from
inside the gateway session.

## What's here

| Path | Purpose |
| ---- | ------- |
| `systemd/opensre-gateway.service` | systemd unit file baked into the AMI. Reads env from `/etc/opensre/gateway.env`. |
| `stack.py` | `GatewayStack` dataclass + helpers to persist AMI id and deployment outputs under `~/.opensre/deployments/`. |
| `bake.py` | `bake_ami()` — launches a temp builder EC2 instance, runs inline install commands via SSM, snapshots an AMI, and terminates the builder. |
| `provision.py` | `provision_gateway_via_ssm()` and `wait_for_gateway_ready()` — writes `/etc/opensre/gateway.env` and restarts the service via SSM. |
| `lifecycle.py` | CLI entrypoint: `bake-ami`, `deploy`, `destroy` subcommands. |

## Commands

Run from the **repo root** (`make install` first).

| Command | What it does |
| ------- | ------------ |
| `make bake-gateway` | Launch temp EC2, install OpenSRE @ current git HEAD, snapshot AMI, save AMI id locally |
| `make deploy-gateway` | Destroy any prior stack, launch EC2 from saved AMI, write env, start service |
| `make destroy-gateway` | Terminate instance, delete IAM profile/role; AMI kept by default |

Equivalent Python entrypoints:

```bash
uv run python -m platform.deployment.gateway.lifecycle bake-ami
uv run python -m platform.deployment.gateway.lifecycle deploy
uv run python -m platform.deployment.gateway.lifecycle destroy
```

### Prerequisites

1. **AWS credentials** — static keys or role via the default boto3 chain.
2. **Permissions** — EC2, SSM, IAM for the deploy account/region. No ECR needed.
3. **Region** — defaults to `us-east-1` (same as main deploy).

### Environment variables

Copy [`.env.deploy.example`](../../../.env.deploy.example) and set the same
`TELEGRAM_BOT_TOKEN`, `LLM_PROVIDER`, and API keys used by the main deploy.

| Variable | Required | Used by |
| -------- | -------- | ------- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway service |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |
| `LLM_PROVIDER` + API key | Yes | Gateway service |
| `OPENSRE_GATEWAY_GIT_REF` | No | Git ref to bake (default: local HEAD SHA) |
| `OPENSRE_GATEWAY_AMI_ID` | No | Skip bake, use existing AMI id |
| `OPENSRE_GATEWAY_DESTROY_PURGE_AMI` | No | Set to `1` to also deregister AMI on destroy |
| `OPENSRE_STACK_SUFFIX` | No | Per-developer resource name suffix |

### What `make deploy-gateway` creates

One stack named `opensre-gateway`:

- **EC2** `t3.micro` in the account default VPC
- **IAM** instance profile — SSM + Bedrock (no ECR needed)
- **systemd** `opensre-gateway.service` running as the `opensre` system user

Outputs written to `~/.opensre/deployments/opensre-gateway.json`.

### Bake once, deploy many times

```bash
# Bake once per code change (takes ~5-10 minutes):
make bake-gateway

# Fast redeploy using the saved AMI id (takes ~2-3 minutes):
make deploy-gateway
make destroy-gateway
make deploy-gateway
```

### Rollback

To roll back to a previously baked AMI:

```bash
OPENSRE_GATEWAY_AMI_ID=ami-<previous-id> make deploy-gateway
```

### Checking the gateway

```bash
# SSH (if EC2_KEY_NAME was set) or SSM session:
aws ssm start-session --target <InstanceId>

# Inside the instance:
sudo systemctl status opensre-gateway
sudo journalctl -u opensre-gateway -f
```

## Persistence

Gateway session state lives in `/var/lib/opensre-gateway/.opensre/` on the instance EBS
root volume.  It survives service restarts and reboots, but **does not** survive a full
`make deploy-gateway` (new instance = fresh disk).  Back up
`/var/lib/opensre-gateway/.opensre/gateway/state.db` before re-deploying if session
continuity matters.

## Differences from the Docker/ECR deploy

| | Docker/ECR (`make deploy`) | Gateway (`make deploy-gateway`) |
| - | - | - |
| What deploys | web + gateway containers | gateway service only |
| Runtime | Docker inside EC2 | systemd on EC2 host |
| Shell access | Inside slim container | Full EC2 host |
| `curl`, `sudo`, `systemctl` | Not available | Available |
| Dependency install | Baked into Docker image | Baked into AMI |
| ECR repository | Required | Not needed |
| Update path | `make build-image && make deploy` | `make bake-gateway && make deploy-gateway` |
