# Test Specification Principles

Principles for `tests/e2e/` pipeline-investigation tests. Real fixtures in
this directory are the canonical usage examples — this file states the rules,
not the code.

## 1. Separation of concerns: pure business logic

`use_case.py` (pipeline business logic) must be completely isolated from test
orchestration/observability code — it should look like real customer code with
no awareness of Tracer/RCA/investigation infrastructure, so tests validate the
agent's ability to investigate production-like failures rather than
instrumented ones. Anti-pattern: mixing test infrastructure into business logic.

## 2. Real end-to-end testing: no mocking

Tests must trigger real failures via actual AWS services, real APIs, and real
infrastructure (CloudWatch, S3, Lambda) — no mocked services or simulated
failures. Mocking here would validate the agent against artificial error
messages instead of what production actually produces.

## 3. Traceable investigation metadata

Every investigation must be decorated with `@traceable` and include
`alert_id`, `pipeline_name`, `correlation_id`/`run_id`, and context-specific
keys (`s3_key`, `log_group`, `function_name`, etc.) so investigation quality
and agent behavior can be tracked and debugged over time.

## 4. Alert factory pattern

All tests must use the `create_alert` factory (not hand-built alert dicts) for
consistent structure. Required fields: `pipeline_name`, `run_name`, `status`,
`timestamp`, `annotations` — including `annotations.context_sources` (see #6).

## 5. Failure-first test design

Tests trigger a real failure, capture complete failure context (logs, metrics,
data), create an alert from that captured context, then invoke the
investigation agent and validate quality (e.g. `validity_score`). The failure
*is* the test case — don't test happy paths or inject failures artificially
after the fact.

## 6. Context source annotations

Alerts must declare which evidence sources are available via
`annotations.context_sources` (comma-separated, e.g. `"s3,lambda,cloudwatch"`).
Valid values: `cloudwatch`, `s3`, `lambda`, `batch`, `tracer_web`, `storage`.
The investigation node uses this to decide which `investigation_actions` to
run, avoiding wasted calls to unavailable services.
