# Setting up AWS bench (one-time)

The benchmark runs on AWS Fargate so you don't tie up your laptop or a
GitHub-hosted runner for hours. This is the six-step setup before
**Benchmark — run on Fargate** can launch anything. Do each step once;
re-runs of the actual benchmark only need step 6.

> **Whenever the upstream HF dataset gets a new revision**, re-run step 5
> (mirror to S3) AND bump `corpus_hf_revision` in `infra/bench/variables.tf`
> (or pass it as `-var=corpus_hf_revision=<new-sha>` to `terraform apply`).

## 1. Apply Terraform

Provisions the ECS cluster, S3 artifact bucket, Secrets Manager entries,
IAM roles, CloudWatch log group, and OIDC trust for the GitHub Actions roles.

```bash
cd infra/bench/
terraform init
terraform apply
```

See [infra/bench/README.md](../../infra/bench/README.md) for backend bucket
+ DynamoDB lock requirements.

## 2. Seed the LLM API keys into AWS Secrets Manager

The container reads keys from Secrets Manager at runtime — never from the
workflow. Add `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`,
`HF_TOKEN` as **GitHub repo secrets** (Settings → Secrets and variables →
Actions → Secrets), then run the seeding workflow once per key:

```bash
gh workflow run benchmark-seed-secret.yml -f secret=anthropic_api_key
gh workflow run benchmark-seed-secret.yml -f secret=openai_api_key
gh workflow run benchmark-seed-secret.yml -f secret=deepseek_api_key
gh workflow run benchmark-seed-secret.yml -f secret=hf_token
```

## 3. Build and push the bench container image to ECR

```bash
gh workflow run benchmark-image.yml
```

This also runs automatically on changes to bench code or the Dockerfile.

## 4. Set the four repo variables

These live under Settings → Secrets and variables → Actions → **Variables**
(not Secrets — they're not sensitive). Grab the values from Terraform:

```bash
cd infra/bench/
echo "BENCH_ECS_CLUSTER             = $(terraform output -raw ecs_cluster_name)"
echo "BENCH_TASK_DEFINITION_FAMILY  = $(terraform output -raw task_definition_family)"
echo "BENCH_SUBNET_IDS              = $(terraform output -json subnet_ids | jq -r 'join(",")')"
echo "BENCH_SECURITY_GROUP_ID       = $(terraform output -raw security_group_id)"
```

Paste each value into a new repo variable with the matching name.
`AWS_ACCOUNT_ID` is already set (the seed-secret workflow uses it).

## 5. Mirror the Cloud-OpsBench corpus to S3

The Fargate task pulls the corpus from S3 at startup (not from Hugging
Face — same-region S3 sync is ~30 s vs ~10 min HF rate-limited download).
Seed the S3 prefix once from a developer machine:

```bash
# From the repo root
export HF_TOKEN=hf_...           # see tests/benchmarks/README.md
make download-cloudopsbench-hf   # local copy of the dataset
make mirror-cloudopsbench-s3     # auto-detects HF revision SHA + uploads
                                 # to s3://cloud-ops-bench-dataset/<sha>/
```

The output prints the HF revision SHA. If it differs from the default in
`infra/bench/variables.tf` (`corpus_hf_revision`), update the default or
override per apply: `terraform apply -var=corpus_hf_revision=<sha>`.

The corpus bucket needs to exist before this step. Create it once if
you don't have it: `aws s3 mb s3://cloud-ops-bench-dataset --region us-east-1`.

## 6. Launch a benchmark

```bash
gh workflow run benchmark-run.yml \
    -f config=tests/benchmarks/configs/example.yml \
    -f dev_mode=true
```

Or use the GitHub UI: Actions → **Benchmark — run on Fargate** → Run workflow.

The workflow exits as soon as the task launches. Watch live logs with:

```bash
aws logs tail /ecs/opensre-bench --follow
```

Artifacts land in the bench S3 bucket under `runs/<date>-<sha>/` when the
task finishes.
