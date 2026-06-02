# Running a benchmark

```bash
# One-time setup
make install
make download-cloudopsbench-hf      # pull the 452-scenario corpus
echo "ANTHROPIC_API_KEY=sk-..." >> .env   # plus OPENAI_API_KEY, DEEPSEEK_API_KEY as needed

# Smoke run on 5 scenarios (dev mode skips integrity gates, still calls real LLMs)
uv run python -m tests.benchmarks._framework.cli run \
    tests/benchmarks/configs/example.yml --dev

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

Trigger from **Actions → "Benchmark run (manual)" → Run workflow**. Fill in
the config path and the dev_mode toggle. Artifacts upload as
`bench-results-<run-id>.zip` (30-day retention).

One-time setup before the first CI run: add repo secrets `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `DEEPSEEK_API_KEY` (only the ones your config needs).
Workflow lives at
[../../.github/workflows/benchmark-run.yml](../../.github/workflows/benchmark-run.yml).
