# Hermes e2e suites

Hermes e2e tests execute the OpenSRE investigation pipeline against fixture-backed
Hermes evidence with `context_sources="hermes"`.

Run only Hermes e2e:

```bash
uv run pytest tests/e2e/hermes -m e2e -v
```

These tests are LLM-credential gated via `has_credentials_for_active_llm_provider()`.


## Surface Attribution (Part 5)

Part 5 extends Hermes RCA evaluation with surface attribution coverage.

Goals:

* identify the failing subsystem family
* select the closest historical analog scenario
* generate actionable diagnostic follow-up questions

### Synthetic Coverage

Primary scenario:

* `050-surface-sprawl-unknown-adapter`

Supporting components:

* `analog_registry.py`
* `surface_scoring.py`
* `adapter_tuples.json`

### Meta Evaluation

Run the attribution meta-suite:

```bash
uv run pytest tests/e2e/hermes/meta/test_surface_sprawl.py -q
```

The current attribution corpus contains 23 deterministic adapter tuples spanning:

* messaging
* provider
* runtime
* orchestration
* memory
* control

The meta-suite validates attribution consistency across all registered tuples.

### Benchmarking

Generate an offline benchmark snapshot:

```bash
uv run python -m tests.synthetic.hermes_rca.run_suite --offline-only --write-history
```

Generate a benchmark report:

```bash
uv run python -m tests.synthetic.hermes_rca.benchmark_report
```
