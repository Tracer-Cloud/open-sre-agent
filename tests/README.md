# Tests Catalog Naming Conventions

## Quick-start commands

| Goal | Command | When to use it |
|---|---|---|
| Run the default unit suite with coverage | `make test-cov` | First thing to run locally; no live infrastructure required. |
| Verify all integration configs and clients | `make verify-integrations` | After adding or changing an integration. |
| Run a live RCA end-to-end test | `make test-rca` | When you need to validate a full investigation against real services. |
| Run a single RCA fixture | `make test-rca FILE=<name>` | When iterating on one specific alert scenario. |
| Run the full suite including e2e | `make test-full` | Pre-release or CI; requires live infrastructure. |
| Run synthetic scenarios (no live infra) | `make test-synthetic` | When testing scenario logic without external service dependencies. |

This document defines semantic naming for the test catalog so test type and environment boundaries are obvious at a glance.

## Top-level taxonomy

- `tests/synthetic/`: synthetic RCA simulations with scored fixtures and deterministic scenario assets.
- `tests/e2e/`: real end-to-end scenarios that execute against real services and infrastructure.
- `tests/deployment/`: deployment validation and infrastructure deployment tests.
- `tests/<domain>/`: unit and integration tests for product modules (for example `cli/`, `tools/`, `integrations/`, `services/`).

## E2E naming rules

- Directory name format: `tests/e2e/<scenario_name>/` where `<scenario_name>` describes system and workload (example: `upstream_lambda`, `kubernetes`).
- Environment-specific test files use explicit filenames:
  - `test_local.py` for local environments.
  - `test_<cloud>.py` for cloud environments (example: `test_eks.py`).
- Use explicit environment-oriented filenames when possible: `test_local.py`, `test_aws.py`, or `test_cloud.py`.

## Synthetic naming rules

- Scenario suite path format: `tests/synthetic/<domain>/<scenario_id>-<slug>/`.
- Scenario ids are numeric and ordered (example: `001-replication-lag`).
- Shared synthetic utilities stay under `tests/synthetic/<domain>/shared/`.

## Telemetry naming rules

- `OTEL_RESOURCE_ATTRIBUTES` values must use semantic catalog names and must not use legacy `test_case_*` values.
- Use `test_case=e2e_<scenario_name>` for e2e scenarios.
- Use `test_case=synthetic_<suite_or_scenario_name>` for synthetic suites when applicable.

## Legacy names

Legacy `test_case_*` path naming under `tests/` is deprecated. Use `tests/e2e/*` and `tests/synthetic/*` only.
