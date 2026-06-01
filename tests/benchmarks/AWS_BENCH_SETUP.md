# Setting up AWS bench (one-time)

The benchmark runs on AWS Fargate so you don't tie up your laptop or a
GitHub-hosted runner for hours. This is the five-step setup before
**Benchmark run (manual)** can launch anything. Do each step once;
re-runs of the actual benchmark only need step 5.

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

## 5. Launch a benchmark

```bash
gh workflow run benchmark-run.yml \
    -f image_tag=latest \
    -f config=tests/benchmarks/configs/example.yml \
    -f dev_mode=true
```

Or use the GitHub UI: Actions → **Benchmark run (manual)** → Run workflow.

The workflow exits as soon as the task launches. Watch live logs with:

```bash
aws logs tail /ecs/opensre-bench --follow
```

Artifacts land in the bench S3 bucket under `runs/<date>-<sha>/` when the
task finishes.
