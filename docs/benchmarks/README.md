# Benchmark

This benchmark runs a fixed subset of synthetic scenarios:
- 001-replication-lag
- 002-connection-exhaustion
- 003-storage-full

Reported metrics:
- duration
- token usage
- estimated LLM cost
- per-scenario pass / fail and aggregate pass-rate (from the synthetic RDS suite, see [Axis 1 vs Axis 2 Gap](#axis-1-vs-axis-2-gap) below)
- TP / FP / TN / FN counts across the suite

## Running benchmarks

From the repository root:

```shell
make benchmark
```

This runs the benchmark suite **and** updates the `## Benchmark` section in
`README.md` with a summary table. The full report is written to
`docs/benchmarks/results.md`.

To update only the README from a previously generated report (no LLM calls):

```shell
make benchmark-update-readme
```

To skip the README update during a benchmark run:

```shell
python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator --no-update-readme
```

## How the README auto-update works

The main `README.md` contains two HTML comment markers:

```html
<!-- BENCHMARK-START -->
...summary content...
<!-- BENCHMARK-END -->
```

After each benchmark run, the content between these markers is replaced with
the latest summary table. The replacement is idempotent — running benchmarks
multiple times replaces the previous results rather than appending duplicates.

This follows the same pattern used by the contributors workflow
(`.github/workflows/contributors.yml`).

A GitHub Actions workflow (`.github/workflows/benchmark-readme.yml`) also
runs automatically when `docs/benchmarks/results.md` changes on `main`,
keeping the README in sync without manual intervention.

## Output files

- `docs/benchmarks/results.md` — full per-case report with detailed metrics
- `README.md` (benchmark section) — compact summary table

## Custom README path

To write the summary to a different README file:

```shell
python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator --readme-path /path/to/README.md
```

## Running selected scenarios

```shell
python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator \
    --scenario 001-replication-lag \
    --scenario 002-connection-exhaustion
```

## Axis 1 vs Axis 2 Gap

The synthetic RDS suite is scored along two axes:

- **Axis 1** runs every scenario with the full mock backend (`FixtureGrafanaBackend`) so the agent receives the complete signal set up front. Pass-rate here measures whether the agent's reasoning chain reaches the right root cause when nothing is hidden.
- **Axis 2** runs the same scenarios with `SelectiveGrafanaBackend`, which records every metric the agent requests and only returns matching series. The agent has to query the right evidence specifically rather than receive everything by default. Pass-rate here measures whether the agent's investigation behaviour is rigorous when the data is not pre-served.

The **gap** is `axis_1_pass_rate - axis_2_pass_rate` in percentage points. A small gap means the agent is asking for the right evidence even when it has to. A large gap means the agent leans on having the data handed to it and misses scenarios when it has to investigate.

### How to run

To produce the underlying axis 1 and axis 2 results today:

```shell
make test-rds-synthetic                                              # Axis 1
pytest -m axis2 tests/synthetic/rds_postgres/test_suite_axis2.py -v  # Axis 2
```

The aggregator that prints overall and per-difficulty-level pass rates plus the gap lives in `_print_gap_report` (`run_suite.py`). Wiring the `--axis2` CLI flag through to that aggregator is tracked separately; until that lands, run the two suites independently and read the gap from the per-difficulty pass-rate lines they emit.

### How to read the report

```
=== Axis 1 vs Axis 2 Gap Report ===
  Axis 1 (all scenarios, full data):   X%  (n/N)
  Axis 2 (adversarial, selective):     Y%  (m/N)
  Gap:                                 +/-Zpp

  Per difficulty level:
    Difficulty 1: Axis1=...% (n scenarios)  Axis2=...% (m scenarios)  gap=+/-Zpp
    Difficulty 2: ...
    Difficulty 3: ...
    Difficulty 4: ...
```

The per-difficulty breakdown is the high-signal piece: difficulty-1 scenarios are usually within a few points across axes, while higher-difficulty cases typically reveal the gap.

### Latest gap

<!-- AXIS-GAP-START -->
The latest numbers are produced by running the two-axis commands above and reading `_print_gap_report` from a local invocation. They are not auto-committed today; the marker pair is here so a future workflow can drop the rendered gap output between them once the `--axis2` CLI wiring lands.
<!-- AXIS-GAP-END -->

The `AXIS-GAP-START` / `AXIS-GAP-END` markers follow the same pattern as the existing `BENCHMARK-START` / `BENCHMARK-END` block in the main `README.md`, so a future workflow can write the latest gap output between them automatically.
