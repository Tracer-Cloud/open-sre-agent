# Running a benchmark

## Where the corpus lives

- **Hugging Face (upstream):** [tracer-cloud/cloud-ops-bench-dataset](https://huggingface.co/datasets/tracer-cloud/cloud-ops-bench-dataset)
- **AWS S3 mirror:** `s3://cloud-ops-bench-dataset/<hf-revision-sha>/` — the same corpus, revision-pinned, used by the Fargate bench task at startup (faster than HF, no rate limits, no `HF_TOKEN` needed at runtime). Populated by `make mirror-cloudopsbench-s3`.

```bash
# One-time setup
make install
make download-cloudopsbench-hf      # pull the 452-scenario corpus
echo "ANTHROPIC_API_KEY=sk-..." >> .env   # plus OPENAI_API_KEY, DEEPSEEK_API_KEY as needed

# Smoke run on 5 scenarios (dev mode skips integrity gates, still calls real LLMs)
uv run python -m tests.benchmarks._framework.cli run \
    tests/benchmarks/configs/cloudopsbench_smoke.yml --dev

# Artifacts land in .bench-results/example/<run-id>/:
#   report.json        ← machine-readable
#   report.md          ← human-readable summary
#   report.html        ← self-contained, open in any browser
#   provenance.json    ← code SHA, config content, env, model versions
#   cases/*.json       ← per-case raw artifacts
```

## Other commands

```bash
uv run python -m tests.benchmarks._framework.cli list        # show available adapters
uv run python -m tests.benchmarks._framework.cli validate <config>   # lint config without running
uv run python -m tests.benchmarks._framework.cli report <run_dir>    # re-render md + html from report.json
```

## Production run (real numbers, not dev mode)

Drop `--dev`. The framework will refuse to start unless a pre-registration
file is committed at the path named in your config. See
[../../docs/cloudopsbench.mdx](../../docs/cloudopsbench.mdx) for the full
guide.

## Running from GitHub CI

Trigger from **Actions → "Benchmark — run on Fargate" → Run workflow**. Fill in
the config path and the dev_mode toggle. The workflow launches an ECS task and
exits in under a minute — the actual bench runs on Fargate. Watch live logs
with `aws logs tail /ecs/opensre-bench --follow`, or via the AWS Console under
ECS → Clusters → opensre-bench → Tasks. Results land in the bench results S3
bucket under `runs/<date>-<sha>/` when the task finishes.

One-time setup before the first CI run: add repo secrets `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `DEEPSEEK_API_KEY` (only the ones your config needs).
Workflow lives at
[../../.github/workflows/benchmark-run.yml](../../.github/workflows/benchmark-run.yml).
