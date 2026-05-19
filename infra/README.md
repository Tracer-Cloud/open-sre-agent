# `infra/`

Infrastructure code for opensre. Most subdirectories are scoped to a single
workstream; if you're looking for the bench environment specifically, jump to
[`infra/bench/`](bench/).

## What's here

| Path | Purpose |
| --- | --- |
| [`bench/`](bench/) | Terraform module for the **Cloud-OpsBench Phase 1** environment on AWS Fargate. Provisions ECR, ECS, IAM, S3, Secrets Manager, CloudWatch. Triggered from GitHub Actions. |
| [`scripts/`](scripts/) | One-time bootstrap scripts (e.g. [`bootstrap-bench-state.sh`](scripts/bootstrap-bench-state.sh) for the Terraform state backend). |
| `docker-compose.*.yml` | Local development environments (database, RabbitMQ, testing). Not related to the AWS bench infra. |
| `install-proxy/`, `opensre-dataset/` | Other infra utilities (unrelated to bench). |

## Cloud-OpsBench infrastructure — resources system design

The bench env provisions a small, focused set of AWS resources. Every resource is Terraform-managed under [`infra/bench/`](bench/) (single account, `us-east-1`).

```
┌─────────────────────────────────────────────────────────────────┐
│ AWS account (us-east-1)                                          │
│                                                                  │
│ Identity & Access                                                │
│ ┌────────────────┐ ┌──────────────────┐ ┌─────────┐ ┌─────────┐│
│ │ OIDC provider  │→│ github-actions   │ │ task    │ │ exec    ││
│ │ (GitHub)       │ │ role (CI)        │ │ role    │ │ role    ││
│ └────────────────┘ └──────────────────┘ └─────────┘ └─────────┘│
│                                                                  │
│ Compute (default VPC, public subnet, outbound-only SG)           │
│ ┌────────┐  ┌────────────┐  ┌──────────────┐                    │
│ │ ECR    │→│ Task def   │→│ Fargate task  │                    │
│ │        │  │            │  │ (ephemeral)   │                    │
│ └────────┘  └────────────┘  └──────────────┘                    │
│                                                                  │
│ Storage & Observability                                          │
│ ┌──────────────┐ ┌────────────────┐ ┌────────────────┐          │
│ │ Secrets Mgr  │ │ S3 results     │ │ CloudWatch     │          │
│ │ × 4 keys     │ │ versioned      │ │ /ecs/opensre-  │          │
│ └──────────────┘ └────────────────┘ │ bench (30d)    │          │
│                                      └────────────────┘          │
│                                                                  │
│ Terraform state backend (bootstrapped out-of-band)               │
│ ┌──────────────┐ ┌────────────────┐                              │
│ │ S3 tfstate   │ │ DynamoDB lock  │                              │
│ └──────────────┘ └────────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

For the full system design — component breakdown, data flows, security model, design decisions, alternatives considered — see [`../opensre-notes/bench-infra-system-design.md`](../../opensre-notes/bench-infra-system-design.md) (outside the repo, per the team's design-doc convention). For an HTML diagram you can open in a browser: [`../opensre-notes/bench-infra-aws-resources.html`](../../opensre-notes/bench-infra-aws-resources.html).

**4 secrets in Secrets Manager** (LLM API keys + HF token; values seeded out-of-band):
- `opensre-bench/llm/anthropic_api_key` (Claude)
- `opensre-bench/llm/openai_api_key` (GPT-4o, GPT-5)
- `opensre-bench/llm/deepseek_api_key` (DeepSeek-V3.2)
- `opensre-bench/llm/hf_token` (Hugging Face dataset download)

**Idle cost:** ~$2/month (Secrets Manager dominates; everything else trivial when no task running).
**Per-run cost:** dominated by LLM API spend (~$1-1.5K for a full Phase 1 grid); Fargate + storage + logs are <$15 of that.

## Integration: GitHub Actions

Two integration points between the repo and AWS, with different auth patterns and trust boundaries.

### A. Terraform plan on PRs

[`.github/workflows/terraform-bench.yml`](../.github/workflows/terraform-bench.yml) runs on every PR that touches `infra/bench/**`:

1. Authenticates with AWS using `secrets.AWS_ACCESS_KEY_ID` / `secrets.AWS_SECRET_ACCESS_KEY` (long-lived keys, same pattern as other workflows in the repo)
2. Runs `terraform fmt -check`, `init`, `validate`, `plan -lock=false`
3. Posts the plan as a **sticky PR comment** so reviewers see what would change
4. Never applies — apply is developer-local in v1

The credentials need read access to AWS resources for plan-time introspection (S3, DynamoDB for state; ECS, ECR, IAM, Secrets Manager, CloudWatch, EC2/VPC for resource diffing). `ReadOnlyAccess` plus state-bucket R/W is the minimal grant.

### B. Bench task launch (run-time, Phase 1+ — pending Dockerfile)

A separate workflow (`.github/workflows/bench.yml`, to land when the Dockerfile does) will:

1. Authenticate with AWS via **GitHub OIDC** (no long-lived credentials)
2. Assume the `opensre-bench-github-actions` IAM role provisioned in [`iam_oidc.tf`](bench/iam_oidc.tf)
3. Call `aws ecs run-task` to launch a one-off Fargate task
4. Poll task status; on completion, sync `s3://tracer-cloud-bench-results/runs/<id>/` and upload as workflow artifact

The OIDC role's trust policy is scoped to `repo:Tracer-Cloud/opensre:*`. Tighten to a specific branch or environment for production runs — hint comment in [`iam_oidc.tf`](bench/iam_oidc.tf).

### GitHub repository secrets required

| Secret | Used by | Source |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | terraform-bench.yml | Pre-existing AWS IAM user (read-mostly) |
| `AWS_SECRET_ACCESS_KEY` | terraform-bench.yml | Same as above |

LLM API keys are **NOT** in GitHub secrets — they live in AWS Secrets Manager and are injected directly into the Fargate task at run time. The bench-runner workflow never sees them.

## Integration: Grafana Cloud

The team uses Grafana Cloud for observability. The bench environment integrates via **Grafana Cloud's native CloudWatch data source** — no AWS infra change, no log shipping, no Fluent Bit sidecar.

### How it works

- Bench logs → CloudWatch (`/ecs/opensre-bench`, 30-day retention). Standard ECS-native `awslogs` driver.
- Grafana Cloud's web UI is configured (out-of-band) with a CloudWatch data source pointed at this AWS account.
- The team queries bench logs through their existing Grafana dashboards using LogQL.

### Setting it up (one-time, Grafana side only)

1. Grafana Cloud → **Connections** → **Add new connection** → **Amazon CloudWatch**
2. Configure with an AWS access key/role that has read access to `/ecs/opensre-bench`:
   - `logs:GetLogEvents`
   - `logs:DescribeLogGroups`
   - `logs:DescribeLogStreams`
   - `logs:StartQuery`, `logs:GetQueryResults` (for Insights queries)
3. Save the data source
4. Create a dashboard or use **Explore** with the new data source selected → set the log group to `/ecs/opensre-bench`

That's it. No Terraform change in this repo, no new secrets, no sidecar.

### Why not ship logs directly to Grafana Loki?

Considered: a Fluent Bit sidecar in the Fargate task definition shipping logs via `awsfirelens` to Grafana Cloud Loki. Rejected because:

- Adds a second container to every task (complexity, image to maintain, more moving parts)
- Requires a 5th secret (Grafana Cloud API key) + 2 new Terraform variables
- Bench is API-bound and runs maybe weekly — the latency/storage difference vs CloudWatch doesn't pay for the extra surface area
- Grafana Cloud's CloudWatch data source gives the team UI alignment with **zero** AWS-side changes

If/when the bench evolves into a hot-path or always-on system, revisit. For Phase 1 (weekly characterization runs), CloudWatch + Grafana data source is the right balance.

## Future modules

When the bench framework merges off its feature branch:

- Container Dockerfile (separate concern; lives in repo root)
- `.github/workflows/bench.yml` (run-time workflow that launches Fargate tasks)
- Pre-registration YAML for Phase 1 ([`tests/benchmarks/configs/preregistrations/`](../tests/benchmarks/configs/preregistrations/))

Other modules that may follow Phase 2 / additional adapters:

- ITBench-SRE adapter (different bench corpus, same infra)
- Phase 2 LLM-alone comparison (different framework code, same infra)
- Apply-from-CI workflow (separate OIDC role with broader IAM)

All reuse `infra/bench/` unchanged.
