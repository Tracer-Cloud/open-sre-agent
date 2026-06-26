# CloudOpsBench

CloudOpsBench runner code lives here. The benchmark corpus itself is hosted on
Hugging Face at `tracer-cloud/cloud-ops-bench-dataset` and is **not** checked
into this repository — it's pulled at run time.

There are two ways to run the benchmark, with two different corpus paths:

| Path | Corpus source | Trigger |
|---|---|---|
| **Local development** | `make download-cloudopsbench-hf` pulls `benchmark/**` from Hugging Face into `tests/benchmarks/cloudopsbench/` once. | `make test-cloudopsbench` runs against the on-disk copy. |
| **AWS Fargate (production runs)** | The ECS task's `entrypoint.sh` runs `aws s3 sync s3://<corpus_bucket>/<hf_revision>/ tests/benchmarks/cloudopsbench/benchmark/` at startup — typically ~30s same-region. The S3 mirror is seeded once per HF revision by `make mirror-cloudopsbench-s3` from a developer machine. | Dispatch the `Benchmark — run on Fargate` workflow. |

## Local development

```bash
make download-cloudopsbench-hf   # one-time corpus pull from HF
make validate-cloudopsbench       # sanity-check the downloaded corpus
make test-cloudopsbench           # run the suite
```

Subset / filter:

```bash
make test-cloudopsbench CLOUDOPSBENCH_LIMIT=10
make test-cloudopsbench SYSTEM=boutique FAULT=service CLOUDOPSBENCH_LIMIT=5
```

Override source repo or destination dir:

```bash
make download-cloudopsbench-hf \
  CLOUDOPSBENCH_HF_DATASET_ID=tracer-cloud/cloud-ops-bench-dataset \
  CLOUDOPSBENCH_DATASET_DIR=/tmp/cloudopsbench

make test-cloudopsbench CLOUDOPSBENCH_BENCHMARK_DIR=/tmp/cloudopsbench/benchmark
```

## AWS Fargate

Bench runs on AWS are end-to-end automated via three workflows. The corpus is
read from S3 (mirrored from HF at a pinned revision), and results land back in
a separate S3 bucket. The bench Docker image is small (~200 MB) because the
~3 GB corpus is **not** baked into the image — it's pulled per task startup.

**Prerequisites** (one-time, per AWS account):
1. `cd infra/bench && terraform apply` — provisions ECR, ECS cluster, IAM roles, S3 buckets, secrets.
2. Seed LLM API keys via `Benchmark secret — seed GitHub repo secret into AWS Secrets Manager` (per provider).
3. Seed the corpus into S3:
   ```bash
   make download-cloudopsbench-hf
   BENCH_S3_BUCKET=$(cd infra/bench && terraform output -raw corpus_bucket_name) \
     make mirror-cloudopsbench-s3
   ```
   The mirror is keyed by HF revision SHA so older revisions remain replayable.

### Running via GitHub Actions UI

Three workflows form a build → promote → run chain. Trigger each from the
**Actions** tab in the GitHub UI ("Run workflow" button on the right).

**Step 1 — Build the bench image.**

Workflow: `Benchmark image — build + push to ECR`

| Input | Value |
|---|---|
| Use workflow from | branch you want measured (`main` or your PR branch) |
| Image tag to push | leave blank → short git SHA is used, or type a custom tag |

What happens: `Dockerfile.bench` is built and pushed to the `opensre-bench`
ECR repository. The workflow logs the final pushed tag — copy it for step 2.

This workflow also fires automatically on every push to `main` that touches
`app/`, `tests/benchmarks/`, `pyproject.toml`, `uv.lock`, or the Dockerfile,
so for main-branch measurements you can skip Step 1 and use the auto-built
tag (look at the most recent green run in the Actions tab).

**Step 2 — Promote the image to a new task-def revision.**

Workflow: `Benchmark image — promote tag to task definition`

| Input | Value |
|---|---|
| Use workflow from | doesn't matter (workflow itself is the same on every ref) |
| ECR image tag to promote | the tag from Step 1, e.g. `48b4283` |

What happens: `terraform apply -target=aws_ecs_task_definition.bench
-var=image_tag=<TAG>` registers a new task-def revision pointing at that
image. Old revisions are retained (`skip_destroy=true`) so you can roll
back by promoting an older tag.

**Step 3 — Run the bench.**

Workflow: `Benchmark — run on Fargate`

| Input | Value |
|---|---|
| Path to YAML config inside the container | one of `tests/benchmarks/cloudopsbench/configs/*.yml`, e.g. `tests/benchmarks/cloudopsbench/configs/cloudopsbench_v1_openai.yml` |
| Dev mode | `true` for ad-hoc runs (skips integrity gates), `false` for publication-grade runs (requires pre-registration) |

What happens: an ECS Fargate task starts using the *current* task-def
revision (the one Step 2 produced). The container's `entrypoint.sh`:

1. Syncs the corpus from `s3://<corpus_bucket>/<hf_revision>/` (env vars
   `BENCH_CORPUS_S3_BUCKET` + `BENCH_CORPUS_HF_REVISION` are set on the
   task def).
2. Invokes the bench CLI against the chosen config.
3. Uploads `report.json`, `provenance.json`, and per-case files to S3.

There is intentionally no `image_tag` input on this workflow — ECS RunTask
cannot override the image URI per-task. To run with a different image, do
Steps 1 + 2 first.

**Results.**

Artifacts land at:

```
s3://<results_bucket>/runs/<config>/<run-id>/
```

— get the bucket name with:

```bash
cd infra/bench && terraform output -raw results_bucket_name
```

Tail live logs while the task runs:

```bash
aws logs tail /ecs/opensre-bench --follow --region us-east-1
```

Or open the ECS task in the AWS Console (link is in the workflow's job
summary).

Filters and case limits live in the bench config YAML
(`tests/benchmarks/cloudopsbench/configs/*.yml`) that you point the workflow at — pick
or copy an existing one to scope the run.

### Updating LLM API keys

To rotate or update the OpenAI / Anthropic / DeepSeek / Hugging Face token:

1. GitHub repo → Settings → Secrets and variables → Actions → update the
   corresponding `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / etc.
2. Run `Benchmark secret — seed GitHub repo secret into AWS Secrets Manager`
   and pick the matching `<provider>_api_key` from the dropdown.

The bench container reads the key from Secrets Manager at task startup, so
the next bench run picks up the new value without rebuilding the image.

### Rolling back

The promote step retains old task-def revisions (`skip_destroy=true`). To
re-run an older image, re-trigger Step 2 with that older tag — terraform
registers a new revision pointing at the same old image, and Step 3 picks
it up. You can also point ECS at a specific old revision directly via
`aws ecs update-service` if you ever want to bypass the workflow.
