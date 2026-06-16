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

## Part 1/5: Provider & Transport Attribution

The provider and transport attribution suite establishes RCA coverage for failures occurring between Hermes and external LLM providers and transport adapters.

Provider-facing failures are among the highest-frequency incident classes in Hermes deployments. These failures often present as generic session crashes, degraded agent behavior, failed requests, or stalled streams even when the root cause originates in provider responses, transport behavior, authentication propagation, or configuration resolution.

This track validates whether investigations can correctly attribute incidents to the provider and transport layer before escalating analysis into runtime, orchestration, memory, or control systems.

The suite evaluates whether an investigation can correctly:

* Distinguish provider-side failures from local runtime failures
* Identify malformed or incomplete provider responses
* Diagnose request validation and payload construction issues
* Recognize provider overload conditions
* Detect provider-selection overrides and configuration drift
* Identify authentication and header propagation failures
* Diagnose streaming transport and SSE protocol issues

### Scenario Coverage

#### Scenario 001: Codex Empty Response

This scenario evaluates whether an investigation can correctly:

* Identify malformed or empty provider responses
* Distinguish provider failures from local runtime failures
* Correlate retry activity with provider-side response issues

#### Scenario 002: OpenRouter 400 Across Models

This scenario evaluates whether an investigation can correctly:

* Identify request validation failures
* Distinguish payload issues from provider outages
* Attribute failures to malformed or unsupported provider requests

#### Scenario 003: MiniMax 529 Overload

This scenario evaluates whether an investigation can correctly:

* Identify upstream provider overload conditions
* Distinguish provider capacity issues from local runtime failures
* Recommend failover to alternative providers

#### Scenario 004: Bedrock IMDS Override

This scenario evaluates whether an investigation can correctly:

* Detect provider-selection overrides
* Correlate runtime metadata with provider routing decisions
* Identify IMDS-driven configuration precedence behavior

#### Scenario 005: Codex Headers Dropped

This scenario evaluates whether an investigation can correctly:

* Identify missing authorization headers
* Diagnose request propagation failures
* Distinguish transport regressions from credential issues

#### Scenario 006: SSE Line Overflow

This scenario evaluates whether an investigation can correctly:

* Identify streaming transport failures
* Diagnose SSE parser limitations
* Distinguish transport-layer failures from provider outages

### Evidence Sources

Provider investigations use:

* `hermes_provider_traffic`
* `hermes_config`
* `hermes_runtime_state`
* `hermes_session_log`

Together these evidence sources provide captured HTTP/SSE traffic, resolved provider configuration, runtime metadata, retry history, and user-visible failure symptoms.

### Investigation Tooling

Part 1 introduces:

* `get_hermes_provider_traffic`
* `get_hermes_config`

These tools allow investigations to reason over provider request and response traffic, provider configuration, transport settings, authentication behavior, and provider-selection decisions.

The provider and transport attribution suite forms the foundation of the Hermes RCA program and provides the baseline investigation patterns used throughout the later runtime, orchestration, memory, control, and surface-attribution tracks.

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
