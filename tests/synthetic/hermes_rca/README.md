# Hermes RCA synthetic suite

This suite is the incident-identification track for Hermes failures.

- Path: `tests/synthetic/hermes_rca/`
- Deterministic checks (no LLM):
  - `uv run python -m tests.synthetic.hermes_rca.run_suite --offline-only`
  - `uv run pytest tests/synthetic/hermes_rca -v`
- LLM-backed RCA checks (optional):
  - `uv run python -m tests.synthetic.hermes_rca.run_suite`

This suite intentionally coexists with the existing `tests/synthetic/hermes/`
log-classifier suite from PR #1860.

## Part 5/5: Surface Attribution Evaluation

The surface attribution suite extends Hermes RCA coverage beyond provider, orchestration, memory, and control failures.

### Scenario 050: Surface Sprawl / Unknown Adapter

This scenario evaluates whether an investigation can correctly:

* Identify the failing surface family
* Attribute an unknown adapter to the closest known subsystem
* Select the closest analog scenario from previous Hermes RCA suites
* Ask a targeted diagnostic follow-up question

### Analog Registry

`analog_registry.py` contains curated analog mappings from Parts 1–4 of the Hermes RCA suite.

The registry allows evaluators to compare a new failure against previously validated scenarios and verify whether attribution remains consistent.

### Adapter Tuple Corpus

`adapter_tuples.json` contains a deterministic set of messaging, provider, execution, memory, orchestration, and control combinations used for attribution testing.

### Benchmark History

Benchmark snapshots can be generated using:

```bash
uv run python -m tests.synthetic.hermes_rca.run_suite --offline-only --write-history
```

Snapshots are stored under:

```text
tests/synthetic/hermes_rca/benchmark_history/
```

and can be summarized with:

```bash
uv run python -m tests.synthetic.hermes_rca.benchmark_report
```
