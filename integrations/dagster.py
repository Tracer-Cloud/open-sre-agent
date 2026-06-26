"""Shared Dagster integration helpers.

Provides configuration, source-dict adapters, validation helpers, and the
four query helpers used by the Dagster tool layer. All operations are
production-safe: read-only, timeouts enforced, result sizes capped via the
helper defaults.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure

if TYPE_CHECKING:
    from vendors.dagster.client import DagsterClient

logger = logging.getLogger(__name__)

DEFAULT_DAGSTER_TIMEOUT_S = 10
DEFAULT_DAGSTER_MAX_RESULTS = 25
DEFAULT_DAGSTER_RUN_LOG_PAGE_SIZE = 250
# Sliding window of the most recent non-failure events kept from a run log;
# bounds LLM context bloat. Older non-failures are evicted so the kept window
# stays adjacent to the (typically later-in-stream) failures, preserving
# diagnostic context. Failure events are ALWAYS retained regardless of this cap.
MAX_NON_FAILURE_RUN_LOG_EVENTS = 1500
# Safety net on pagination depth; bounds HTTP latency for outsized runs.
# 100 pages * 250 events = up to 25,000 events scanned .
MAX_RUN_LOG_PAGES = 100
_FAILURE_EVENT_TYPES = frozenset({"ExecutionStepFailureEvent", "RunFailureEvent"})


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
    from vendors.dagster.client import DagsterClient  # lazy import to avoid circular dependency

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
    from vendors.dagster.client import DagsterClient

    return DagsterClient(
        endpoint=config.endpoint,
        api_token=config.api_token,
        timeout_s=DEFAULT_DAGSTER_TIMEOUT_S,
    )


def _compute_run_durations(runs_result: dict[str, Any]) -> dict[str, Any]:
    """Mutate ``runs_result`` to add ``duration_seconds`` per row (``None`` for runs
    still in flight); returns the same dict for chainability. No-op on non-``Runs``
    union members (``InvalidPipelineRunsFilterError``, ``PythonError``).
    """
    data = runs_result.get("data") or {}
    runs_or_error = data.get("runsOrError") or {}
    if runs_or_error.get("__typename") != "Runs":
        return runs_result
    for run in runs_or_error.get("results") or []:
        start = run.get("startTime")
        end = run.get("endTime")
        run["duration_seconds"] = end - start if start is not None and end is not None else None
    return runs_result


def list_runs(
    config: DagsterConfig,
    *,
    limit: int = DEFAULT_DAGSTER_MAX_RESULTS,
    status: str | None = None,
    job_name: str | None = None,
) -> dict[str, Any]:
    """List recent Dagster runs, optionally filtered by ``status`` and/or ``job_name``."""
    with _client(config) as c:
        result = c.list_runs(limit=limit, status=status, job_name=job_name)
    return _compute_run_durations(result)


def _event_timestamp(event: dict[str, Any]) -> float:
    """Numeric timestamp for sorting; defaults to 0.0 for missing/malformed values."""
    ts = event.get("timestamp")
    if ts is None:
        return 0.0
    try:
        return float(ts)
    except (ValueError, TypeError):
        return 0.0


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
    """Fetch event logs for a run; failure events are kept in full, non-failure
    events are held in a sliding window of the most recent
    ``MAX_NON_FAILURE_RUN_LOG_EVENTS`` to bound LLM context while keeping the
    kept events adjacent to failures (which typically land later in the
    stream). Pagination continues until ``hasMore=false`` so all failures
    in the run are surfaced; ``MAX_RUN_LOG_PAGES`` is a safety net for
    outsized runs.

    A mid-pagination error preserves the failures already collected and surfaces
    ``summary.fetch_error`` so callers know the data is partial.
    """
    failure_events: list[dict[str, Any]] = []
    non_failure_events: deque[dict[str, Any]] = deque(maxlen=MAX_NON_FAILURE_RUN_LOG_EVENTS)
    non_failure_seen = 0
    last_cursor: str | None = None
    cursor: str | None = None
    pages_fetched = 0
    page_cap_reached = False
    fetch_error: str | None = None

    with _client(config) as c:
        while True:
            if pages_fetched >= MAX_RUN_LOG_PAGES:
                page_cap_reached = True
                break
            page = c.get_run_logs(
                run_id=run_id, limit=DEFAULT_DAGSTER_RUN_LOG_PAGE_SIZE, cursor=cursor
            )
            pages_fetched += 1
            if "error" in page:
                if pages_fetched == 1:
                    # First-page: nothing collected yet,
                    # propagate the raw envelope as-is.
                    return page
                # Mid-pagination error: preserve accumulated failures, signal
                # partial fetch via summary.fetch_error.
                fetch_error = page["error"]
                break
            data = page.get("data") or {}
            logs_for_run = data.get("logsForRun") or {}
            if logs_for_run.get("__typename") != "EventConnection":
                if pages_fetched == 1:
                    # First-page non-event response (e.g. RunNotFoundError,
                    # PythonError): nothing collected yet, propagate as-is.
                    return page
                # Mid-pagination non-event response: preserve accumulated failures,
                # signal partial via summary.fetch_error.
                fetch_error = (
                    f"unexpected response type on page {pages_fetched}: "
                    f"{logs_for_run.get('__typename')}"
                )
                break
            for event in logs_for_run.get("events") or []:
                if event.get("__typename") in _FAILURE_EVENT_TYPES:
                    failure_events.append(event)
                else:
                    non_failure_seen += 1
                    non_failure_events.append(event)  # deque auto-evicts oldest when full
            last_cursor = logs_for_run.get("cursor")
            if not logs_for_run.get("hasMore"):
                break
            cursor = last_cursor
            if cursor is None:
                fetch_error = (
                    f"server returned hasMore=true but no cursor on page {pages_fetched}; "
                    "event log may be incomplete"
                )
                break

    window_overflowed = non_failure_seen > MAX_NON_FAILURE_RUN_LOG_EVENTS
    truncated = window_overflowed or page_cap_reached or (fetch_error is not None)
    # Sort by timestamp to preserve causal chronology. otherwise, a
    # downstream skip event in non_failure_events would appear BEFORE
    # the upstream failure in failure_events in the returned array
    aggregated_events = sorted(list(non_failure_events) + failure_events, key=_event_timestamp)
    aggregated = {
        "__typename": "EventConnection",
        "events": aggregated_events,
        "cursor": last_cursor,
        "hasMore": truncated,
    }
    summary = _extract_step_failures({"events": failure_events})
    summary["events_examined"] = len(aggregated_events)
    summary["truncated"] = truncated
    if fetch_error is not None:
        summary["fetch_error"] = fetch_error
    return {"data": {"logsForRun": aggregated}, "summary": summary}


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


def list_schedule_ticks(
    config: DagsterConfig,
    *,
    repository_name: str,
    repository_location_name: str,
    schedule_name: str,
    limit: int = DEFAULT_DAGSTER_MAX_RESULTS,
) -> dict[str, Any]:
    """Fetch recent tick history for a Dagster schedule."""
    with _client(config) as c:
        return c.list_schedule_ticks(
            repository_name=repository_name,
            repository_location_name=repository_location_name,
            schedule_name=schedule_name,
            limit=limit,
        )


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[DagsterConfig | None, str | None]:
    try:
        cfg = build_dagster_config(
            {
                "endpoint": credentials.get("endpoint", ""),
                "api_token": credentials.get("api_token", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="dagster", record_id=record_id)
        return None, None
    if cfg.endpoint:
        return cfg, "dagster"
    return None, None
