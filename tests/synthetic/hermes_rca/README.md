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
