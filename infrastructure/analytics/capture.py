"""Emit API for analytics events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from infrastructure.analytics.events import Event
from infrastructure.analytics.provider import Properties, get_analytics
from infrastructure.observability.errors.sentry import capture_exception
from infrastructure.analytics.investigation_tracker import InvestigationTracker
from infrastructure.analytics.event_properties import (
    _bucket_duration_ms,
    _bucket_percentage,
    _integration_lifecycle_properties,
    _investigation_completed_properties,
    _investigation_failed_properties,
    _investigation_outcome_properties,
    _onboard_completed_properties,
    _with_investigation_loop_metrics,
)

EVAL_AND_TERMINAL_KPI_QUERIES: Final[dict[str, str]] = {
    "terminal_action_execution_success_rate": """
SELECT
  round(
    100.0 * sum(toFloat64OrNull(properties.executed_success_count)) /
    nullIf(sum(toFloat64OrNull(properties.executed_count)), 0),
    2
  ) AS terminal_action_execution_success_rate
FROM events
WHERE event = 'terminal_actions_executed'
""".strip(),
    "terminal_fallback_rate": """
SELECT
  round(
    100.0 * countIf(
      event = 'terminal_turn_summarized'
      AND (properties.fallback_to_llm = true OR properties.fallback_to_llm = 'true')
    ) /
    nullIf(countIf(event = 'terminal_turn_summarized'), 0),
    2
  ) AS terminal_fallback_rate
FROM events
WHERE event = 'terminal_turn_summarized'
""".strip(),
}

EVAL_AND_TERMINAL_EVENT_CONTRACT: Final[dict[Event, frozenset[str]]] = {
    Event.TERMINAL_ACTIONS_PLANNED: frozenset({"planned_count", "has_unhandled_clause"}),
    Event.TERMINAL_ACTIONS_EXECUTED: frozenset(
        {"planned_count", "executed_count", "executed_success_count", "success_rate_bucket"}
    ),
    Event.TERMINAL_TURN_SUMMARIZED: frozenset(
        {
            "planned_count",
            "executed_count",
            "executed_success_count",
            "fallback_to_llm",
            "session_turn_index",
            "session_fallback_count",
            "session_action_success_bucket",
            "session_fallback_rate_bucket",
        }
    ),
}


def _capture(event: Event, properties: Properties | None = None) -> None:
    try:
        get_analytics().capture(event, properties)
    except Exception as exc:
        capture_exception(exc)


def capture_investigation_lifecycle_event(
    event: Event,
    properties: Properties,
    *,
    state: Mapping[str, object] | None = None,
    tracker: InvestigationTracker | None = None,
    loop_count: int | None = None,
    iteration_cap: int | None = None,
) -> None:
    """Capture an investigation lifecycle event with canonical loop metrics."""
    _capture(
        event,
        _with_investigation_loop_metrics(
            properties,
            loop_count=loop_count,
            iteration_cap=iteration_cap,
            state=state,
            tracker=tracker,
        ),
    )


def capture_cli_invoked(properties: Properties | None = None) -> None:
    # Whole-process default for local CLI; gateway binds surface per turn instead.
    try:
        from infrastructure.analytics.usage_context import UsageSurface, ensure_process_session_id

        analytics = get_analytics()
        analytics.set_persistent_property("surface", UsageSurface.CLI)
        ensure_process_session_id()
        analytics.capture(Event.CLI_INVOKED, properties)
    except Exception as exc:
        capture_exception(exc)


def capture_gateway_turn_started(*, surface: str) -> None:
    """Mark the start of one Slack/Telegram gateway agent turn."""
    _capture(Event.GATEWAY_TURN_STARTED, {"surface": surface})


def capture_gateway_turn_completed(
    *,
    surface: str,
    duration_ms: float,
    answered: bool,
    final_intent: str | None = None,
) -> None:
    """Mark successful completion of one gateway agent turn."""
    props: Properties = {
        "surface": surface,
        "duration_ms": round(duration_ms),
        "duration_bucket": _bucket_duration_ms(duration_ms),
        "answered": answered,
    }
    if final_intent:
        props["final_intent"] = final_intent
    _capture(Event.GATEWAY_TURN_COMPLETED, props)


def capture_gateway_turn_failed(
    *,
    surface: str | None,
    duration_ms: float,
    error_type: str,
) -> None:
    """Mark a failed gateway agent turn (exception during dispatch).

    ``surface`` may be omitted when transport context was unbound so failures
    still land in PostHog for regression detection.
    """
    props: Properties = {
        "duration_ms": round(duration_ms),
        "duration_bucket": _bucket_duration_ms(duration_ms),
        "error_type": error_type,
        "surface_missing": not bool(surface),
    }
    if surface:
        props["surface"] = surface
    _capture(Event.GATEWAY_TURN_FAILED, props)


def capture_repl_execution_policy_decision(properties: Properties | None = None) -> None:
    _capture(Event.REPL_EXECUTION_POLICY_DECISION, properties)


def capture_onboard_started() -> None:
    _capture(Event.ONBOARD_STARTED)


def capture_onboard_completed(config: Mapping[str, object]) -> None:
    _capture(Event.ONBOARD_COMPLETED, _onboard_completed_properties(config))


def capture_onboard_failed() -> None:
    _capture(Event.ONBOARD_FAILED)


def capture_diagnosis_category_mismatch(
    *,
    root_cause_category: str,
    mismatch_reason: str | None = None,
) -> None:
    properties: Properties = {
        "category_text_mismatch": True,
        "root_cause_category": root_cause_category,
    }
    if mismatch_reason:
        properties["mismatch_reason"] = mismatch_reason
    _capture(Event.DIAGNOSIS_CATEGORY_MISMATCH, properties)


def capture_investigation_completed(*, tracker: InvestigationTracker | None = None) -> None:
    if tracker is None:
        _capture(Event.INVESTIGATION_COMPLETED)
        return
    if tracker.completed:
        return
    if tracker.failed or not tracker.enabled:
        return
    _capture(
        Event.INVESTIGATION_COMPLETED,
        _investigation_completed_properties(
            shared_properties=tracker.shared_properties,
            tracker=tracker,
        ),
    )
    tracker.completed = True


def capture_investigation_failed(
    *,
    tracker: InvestigationTracker | None = None,
    failure_type: str | None = None,
    failure_message: str | None = None,
    failure_detail: str | None = None,
    failure_category: str | None = None,
    integration_involved: str | None = None,
    integration_failure_message: str | None = None,
    investigation_target: str | None = None,
    shared_properties: Properties | None = None,
    state: Mapping[str, object] | None = None,
) -> None:
    props = _investigation_failed_properties(
        shared_properties=shared_properties or (tracker.shared_properties if tracker else {}),
        failure_type=failure_type,
        failure_message=failure_message,
        failure_detail=failure_detail,
        failure_category=failure_category,
        integration_involved=integration_involved,
        integration_failure_message=integration_failure_message,
        investigation_target=investigation_target,
        state=state,
        tracker=tracker,
    )
    if tracker is None:
        _capture(Event.INVESTIGATION_FAILED, props)
        return
    if tracker.failed or not tracker.enabled:
        tracker.failed = True
        return
    _capture(Event.INVESTIGATION_FAILED, props)
    tracker.failed = True


def capture_investigation_cancelled(
    *,
    investigation_id: str,
    investigation_target: str = "",
    tracker: InvestigationTracker | None = None,
    state: Mapping[str, object] | None = None,
) -> None:
    shared = tracker.shared_properties if tracker is not None and tracker.enabled else {}
    if investigation_id and not shared.get("investigation_id"):
        shared = {**shared, "investigation_id": investigation_id}
    properties: Properties = {
        **shared,
        "failure_category": "user_cancelled",
    }
    if investigation_target:
        properties["investigation_target"] = investigation_target
    capture_investigation_lifecycle_event(
        Event.INVESTIGATION_CANCELLED,
        properties,
        state=state,
        tracker=tracker,
    )


def capture_investigation_outcome(
    *,
    investigation_id: str,
    status: str,
    investigation_target: str,
    root_cause_excerpt: str = "",
    error_excerpt: str = "",
    failure_category: str | None = None,
    integration_involved: str | None = None,
    integration_failure_message: str | None = None,
    failure_detail: str | None = None,
    state: Mapping[str, object] | None = None,
) -> None:
    if not investigation_id:
        return
    _capture(
        Event.INVESTIGATION_OUTCOME,
        _investigation_outcome_properties(
            investigation_id=investigation_id,
            status=status,
            investigation_target=investigation_target,
            root_cause_excerpt=root_cause_excerpt,
            error_excerpt=error_excerpt,
            failure_category=failure_category,
            integration_involved=integration_involved,
            integration_failure_message=integration_failure_message,
            failure_detail=failure_detail,
            state=state,
        ),
    )


def capture_integration_setup_started(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_STARTED, _integration_lifecycle_properties(service))


def capture_integration_setup_completed(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_COMPLETED, _integration_lifecycle_properties(service))


def capture_integrations_listed() -> None:
    _capture(Event.INTEGRATIONS_LISTED)


def capture_integration_removed(service: str) -> None:
    _capture(Event.INTEGRATION_REMOVED, _integration_lifecycle_properties(service))


def capture_integration_verified(service: str) -> None:
    _capture(Event.INTEGRATION_VERIFIED, _integration_lifecycle_properties(service))


def capture_loop_suggestion_prompted() -> None:
    """Exposure event: the suggested-loops startup picker was rendered."""
    _capture(Event.LOOP_SUGGESTION_PROMPTED)


def capture_loop_suggestion_selected(*, option: str) -> None:
    """User picked one of the suggested loop options (ci_cd / task_management / daily_brief)."""
    _capture(Event.LOOP_SUGGESTION_SELECTED, {"option": option})


def capture_loop_suggestion_skipped() -> None:
    """User dismissed the suggested-loops picker (Escape) without choosing."""
    _capture(Event.LOOP_SUGGESTION_SKIPPED)


def capture_tests_picker_opened() -> None:
    _capture(Event.TESTS_PICKER_OPENED)


def capture_test_synthetic_started(scenario: str, *, mock_grafana: bool) -> None:
    _capture(
        Event.TEST_SYNTHETIC_STARTED,
        {"scenario": scenario, "mock_grafana": mock_grafana},
    )


def capture_test_synthetic_completed(scenario: str, *, exit_code: int) -> None:
    _capture(Event.TEST_SYNTHETIC_COMPLETED, {"scenario": scenario, "exit_code": exit_code})


def capture_test_synthetic_failed(scenario: str, *, reason: str) -> None:
    _capture(Event.TEST_SYNTHETIC_FAILED, {"scenario": scenario, "reason": reason})


def capture_tests_listed(category: str, *, search: bool) -> None:
    _capture(Event.TESTS_LISTED, {"category": category, "search": search})


def capture_test_run_started(test_id: str, *, dry_run: bool) -> None:
    _capture(Event.TEST_RUN_STARTED, {"test_id": test_id, "dry_run": dry_run})


def capture_test_run_completed(test_id: str, *, dry_run: bool, exit_code: int) -> None:
    _capture(
        Event.TEST_RUN_COMPLETED,
        {
            "test_id": test_id,
            "dry_run": dry_run,
            "exit_code": exit_code,
        },
    )


def capture_test_run_failed(test_id: str, *, dry_run: bool, reason: str) -> None:
    _capture(
        Event.TEST_RUN_FAILED,
        {
            "test_id": test_id,
            "dry_run": dry_run,
            "reason": reason,
        },
    )


def capture_terminal_actions_planned(*, planned_count: int, has_unhandled_clause: bool) -> None:
    _capture(
        Event.TERMINAL_ACTIONS_PLANNED,
        {
            "planned_count": planned_count,
            "has_unhandled_clause": has_unhandled_clause,
        },
    )


def capture_terminal_actions_executed(
    *,
    planned_count: int,
    executed_count: int,
    executed_success_count: int,
) -> None:
    success_percent = 100.0 * executed_success_count / executed_count if executed_count > 0 else 0.0
    _capture(
        Event.TERMINAL_ACTIONS_EXECUTED,
        {
            "planned_count": planned_count,
            "executed_count": executed_count,
            "executed_success_count": executed_success_count,
            "success_rate_bucket": _bucket_percentage(success_percent),
        },
    )


def capture_react_turn_completed(
    *,
    phase: str,
    llm_iterations_used: int,
    llm_iteration_cap: int,
    hit_iteration_cap: bool,
    stop_reason: str,
    tool_calls_executed: int,
    duration_ms: int,
    cli_session_id: str,
    cli_turn_kind: str,
    llm_provider: str,
    llm_model: str,
    investigation_id: str | None = None,
    investigation_loop_count: int | None = None,
    prompt_turn_id: str | None = None,
) -> None:
    properties: Properties = {
        "phase": phase,
        "llm_iterations_used": llm_iterations_used,
        "llm_iteration_cap": llm_iteration_cap,
        "hit_iteration_cap": hit_iteration_cap,
        "stop_reason": stop_reason,
        "tool_calls_executed": tool_calls_executed,
        "duration_ms": duration_ms,
        "cli_session_id": cli_session_id,
        "cli_turn_kind": cli_turn_kind,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
    }
    if investigation_id:
        properties["investigation_id"] = investigation_id
    if investigation_loop_count is not None:
        properties["investigation_loop_count"] = investigation_loop_count
    if prompt_turn_id:
        properties["prompt_turn_id"] = prompt_turn_id
    _capture(Event.REACT_TURN_COMPLETED, properties)


def capture_terminal_turn_summarized(
    *,
    planned_count: int,
    executed_count: int,
    executed_success_count: int,
    fallback_to_llm: bool,
    session_turn_index: int,
    session_fallback_count: int,
    session_action_success_percent: float,
    session_fallback_rate_percent: float,
) -> None:
    _capture(
        Event.TERMINAL_TURN_SUMMARIZED,
        {
            "planned_count": planned_count,
            "executed_count": executed_count,
            "executed_success_count": executed_success_count,
            "fallback_to_llm": fallback_to_llm,
            "session_turn_index": session_turn_index,
            "session_fallback_count": session_fallback_count,
            "session_action_success_bucket": _bucket_percentage(session_action_success_percent),
            "session_fallback_rate_bucket": _bucket_percentage(session_fallback_rate_percent),
        },
    )


def capture_update_started(*, check_only: bool) -> None:
    _capture(Event.UPDATE_STARTED, {"check_only": check_only})


def capture_update_completed(*, check_only: bool, updated: bool) -> None:
    _capture(Event.UPDATE_COMPLETED, {"check_only": check_only, "updated": updated})


def capture_update_failed(*, check_only: bool, reason: str) -> None:
    _capture(Event.UPDATE_FAILED, {"check_only": check_only, "reason": reason})


def capture_agent_secret_detected(
    *,
    rule_names: tuple[str, ...],
    count: int,
    blocked: bool,
) -> None:
    _capture(
        Event.AGENT_SECRET_DETECTED,
        {"rule_names": ",".join(rule_names), "count": count, "blocked": blocked},
    )
