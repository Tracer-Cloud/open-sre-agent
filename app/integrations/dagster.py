"""Shared Dagster integration helpers.

Provides configuration, source-dict adapters, validation helpers, and the
four query helpers used by the Dagster tool layer. All operations are
production-safe: read-only, timeouts enforced, result sizes capped via the
helper defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.services.dagster import DagsterClient
from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_DAGSTER_TIMEOUT_S = 10
DEFAULT_DAGSTER_MAX_RESULTS = 25


class DagsterConfig(StrictConfigModel):
    """Normalized Dagster credentials used by resolution and verification flows."""

    endpoint: str
    api_token: str = ""
    integration_id: str = ""


@dataclass(frozen=True)
class DagsterValidationResult:
    """Result of validating a Dagster integration."""

    ok: bool
    detail: str


def build_dagster_config(raw: dict[str, Any] | None) -> DagsterConfig:
    """Build a normalized Dagster config object from env/store data."""
    return DagsterConfig.model_validate(raw or {})


def validate_dagster_config(config: DagsterConfig) -> DagsterValidationResult:
    """Validate Dagster GraphQL reachability with a lightweight version query."""
    if not config.endpoint:
        return DagsterValidationResult(ok=False, detail="Dagster endpoint is required.")

    with DagsterClient(
        endpoint=config.endpoint,
        api_token=config.api_token,
        timeout_s=DEFAULT_DAGSTER_TIMEOUT_S,
    ) as client:
        probe = client.ping()
    if "error" in probe:
        return DagsterValidationResult(
            ok=False, detail=f"Dagster GraphQL probe failed: {probe['error']}"
        )
    data = probe.get("data") or {}
    version = data.get("version")
    if not version:
        return DagsterValidationResult(
            ok=False,
            detail="Dagster GraphQL endpoint responded but did not return a version string.",
        )
    return DagsterValidationResult(ok=True, detail=f"Connected to Dagster version {version}.")


def dagster_is_available(sources: dict[str, dict]) -> bool:
    """Return True when Dagster credentials are configured in the sources dict."""
    dagster = sources.get("dagster") or {}
    return bool(dagster.get("endpoint"))


def dagster_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Dagster connection params from sources for tool invocation."""
    dagster = sources.get("dagster") or {}
    return {
        "endpoint": dagster.get("endpoint", ""),
        "api_token": dagster.get("api_token", ""),
    }


def _client(config: DagsterConfig) -> DagsterClient:
    return DagsterClient(
        endpoint=config.endpoint,
        api_token=config.api_token,
        timeout_s=DEFAULT_DAGSTER_TIMEOUT_S,
    )


def list_runs(
    config: DagsterConfig,
    *,
    limit: int = DEFAULT_DAGSTER_MAX_RESULTS,
    status: str | None = None,
    job_name: str | None = None,
) -> dict[str, Any]:
    """List recent Dagster runs, optionally filtered by ``status`` and/or ``job_name``."""
    with _client(config) as c:
        return c.list_runs(limit=limit, status=status, job_name=job_name)


def _extract_step_failures(logs_for_run: dict[str, Any]) -> dict[str, Any]:
    """Roll up step-level failures from a Dagster event log.

    Returns ``{"failure_count": int, "failures": [...]}`` with step_key,
    timestamp, wrapper_class, exception_class, and cause_message per entry.
    Pre-counting keeps the agent from fixating on the first failure in
    parallel-execution runs.
    """
    events = logs_for_run.get("events") or []
    failures: list[dict[str, Any]] = []
    for event in events:
        if event.get("__typename") != "ExecutionStepFailureEvent":
            continue
        error = event.get("error") or {}
        cause = error.get("cause") or {}
        failures.append(
            {
                "step_key": event.get("stepKey"),
                "timestamp": event.get("timestamp"),
                "wrapper_class": error.get("className"),
                "exception_class": cause.get("className"),
                "cause_message": cause.get("message"),
            }
        )
    return {"failure_count": len(failures), "failures": failures}


def get_run_logs(config: DagsterConfig, *, run_id: str) -> dict[str, Any]:
    """Fetch event logs for a run; enriches the response with a ``summary`` field
    pre-counting step failures (see ``_extract_step_failures``).
    """
    with _client(config) as c:
        result = c.get_run_logs(run_id=run_id)
    data = result.get("data") or {}
    logs_for_run = data.get("logsForRun") or {}
    if logs_for_run.get("__typename") == "EventConnection":
        result["summary"] = _extract_step_failures(logs_for_run)
    return result


def list_assets_with_materialization(
    config: DagsterConfig, *, limit: int = DEFAULT_DAGSTER_MAX_RESULTS
) -> dict[str, Any]:
    """List Dagster assets and their latest materialization status."""
    with _client(config) as c:
        return c.list_assets_with_materialization(limit=limit)


def list_sensor_ticks(
    config: DagsterConfig,
    *,
    repository_name: str,
    repository_location_name: str,
    sensor_name: str,
    limit: int = DEFAULT_DAGSTER_MAX_RESULTS,
) -> dict[str, Any]:
    """Fetch recent tick history for a Dagster sensor."""
    with _client(config) as c:
        return c.list_sensor_ticks(
            repository_name=repository_name,
            repository_location_name=repository_location_name,
            sensor_name=sensor_name,
            limit=limit,
        )
