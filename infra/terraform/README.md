# OpenSRE AI assistant backend on ECS Fargate (Terraform)

Deploys the OpenSRE AI assistant backend — the HTTP API plus the Slack chat
gateway — as two Fargate services in the default VPC:

- **web** — `MODE=web`, serves `/health`, `/alerts`, `/investigate` on the web
  port (public IP, ingress restricted by `web_ingress_cidr`).
- **gateway** — `MODE=gateway`, runs the background agent including the Slack
  Socket Mode worker and Telegram poller. Egress only; Socket Mode needs no
  inbound port.

Secrets (LLM API keys, Slack tokens) are stored as SSM SecureString parameters
and injected by ECS at task start; they never appear in the task definition.

## Prerequisites

1. An image in ECR — from the repo root: `make build-image` (prints/saves the
   image URI).
2. A Slack app with Socket Mode enabled: bot token (`xoxb-…`, scopes
   `app_mentions:read`, `chat:write`, `im:history`) and app-level token
   (`xapp-…`, scope `connections:write`), with the `app_mention` and
   `message.im` events subscribed.

## Usage

```bash
cd infra/terraform
terraform init

terraform apply \
  -var image_uri="<account>.dkr.ecr.us-east-1.amazonaws.com/opensre:latest" \
  -var slack_bot_token="xoxb-…" \
  -var slack_app_token="xapp-…" \
  -var slack_allowed_users="U0123456,U0654321" \
  -var anthropic_api_key="sk-ant-…"
```

Prefer a `terraform.tfvars` file for the non-secret variables (see
`terraform.tfvars.example`); pass tokens via `TF_VAR_slack_bot_token`-style
environment variables so they stay out of files and out of git (`*.tfvars` is
gitignored). **Never commit real tokens.**

`slack_allowed_users` is required unless `slack_allow_open_workspace=true`
(dogfood only). The gateway task receives Slack + LLM secrets only — not
`DATABASE_URL` or the alert listener token.

## Securing the API

- `certificate_arn` — pass an ACM certificate ARN to put an ALB with HTTPS
  (TLS 1.3, HTTP→HTTPS redirect) in front of the web service. Direct container
  access is then closed: the web port only accepts traffic from the ALB. The
  `web_endpoint` output is the URL to point your DNS record at. Without it,
  the web service is plain HTTP on a dynamic public IP — dev only.
- `alert_listener_token` — bearer token required by `/alerts` and
  `/investigate`. Leave empty to keep those routes loopback-only (unusable
  remotely, but closed).
- `/api/*` routes verify Clerk JWTs in the application itself; nothing to
  configure here.
- `web_ingress_cidr` — restrict who can reach the API at the network level.

API usage and auth details: `docs/api.mdx`.

## Teams and isolation

Each customer team gets its own stack: run `terraform apply` per team with a
distinct `name_prefix` (and a separate state file or workspace, e.g.
`terraform workspace new <team>`). Every team then has independent services,
secrets, logs, and agent memory:

- Slack conversation memory is additionally keyed by workspace inside the app
  (`team_id:channel:thread`), so nothing leaks across workspaces even within
  one deployment.
- `/api/*` investigation records are scoped to the caller's Clerk organization.
- A single deployment serves one Slack workspace's tokens; serving many
  workspaces from one deployment needs the OAuth install flow + shared token
  store (webapp track) and is not part of this module.

## Operations

- Logs: `aws logs tail /opensre/gateway --follow` (and `/opensre/web`).
- New image: rerun `make build-image`, then `terraform apply` — a changed
  `image_uri` rolls both services. With an unchanged `:latest` tag, force a
  redeploy: `aws ecs update-service --cluster opensre --service opensre-gateway --force-new-deployment`.
- Rotate a Slack token: `terraform apply` with the new value; ECS injects it on
  the next task start.
- Tear down: `terraform destroy` (the ECR repository is not managed here and
  stays).

## Limits of this setup

- The web service gets a dynamic public IP per task — fine for health checks
  and machine callers that resolve it via ECS; put an ALB and a domain in front
  before pointing browsers or Slack Events API (not needed for Socket Mode) at it.
- Session state (SQLite bindings, session files) lives on ephemeral task
  storage and resets when the gateway task is replaced.
