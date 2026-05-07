"""CLI analytics helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from app.analytics.events import Event
from app.analytics.provider import Properties, get_analytics
from app.utils.sentry_sdk import capture_exception

EVAL_AND_TERMINAL_KPI_QUERIES: Final[dict[str, str]] = {
    "eval_pass_rate": """
SELECT
  round(
    100.0 * countIf(
      event = 'eval_process_completed'
      AND (properties.overall_pass = true OR properties.overall_pass = 'true')
    ) /
    nullIf(countIf(event = 'eval_process_completed'), 0),
    2
  ) AS eval_pass_rate
FROM events
WHERE event IN ('eval_process_completed')
""".strip(),
    "eval_latency_p50_p95_ms": """
SELECT
  quantile(0.50)(toFloat64OrNull(properties.duration_ms)) AS eval_latency_p50_ms,
  quantile(0.95)(toFloat64OrNull(properties.duration_ms)) AS eval_latency_p95_ms
FROM events
WHERE event = 'eval_process_completed'
""".strip(),
    "eval_parse_error_rate": """
SELECT
  round(
    100.0 * countIf(event = 'eval_process_parse_failed') /
    nullIf(countIf(event IN ('eval_process_completed', 'eval_process_parse_failed')), 0),
    2
  ) AS eval_parse_error_rate
FROM events
WHERE event IN ('eval_process_completed', 'eval_process_parse_failed')
""".strip(),
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
    Event.EVAL_PROCESS_STARTED: frozenset(
        {
            "rubric_present",
            "rubric_length_bucket",
            "mode",
        }
    ),
    Event.EVAL_PROCESS_COMPLETED: frozenset(
        {
            "duration_bucket",
            "duration_ms",
            "overall_pass",
            "score_bucket",
            "rubric_item_count",
            "mode",
        }
    ),
    Event.EVAL_PROCESS_FAILED: frozenset(
        {
            "duration_bucket",
            "duration_ms",
            "failure_stage",
            "failure_type",
            "mode",
        }
    ),
    Event.EVAL_PROCESS_SKIPPED: frozenset({"skip_reason", "mode"}),
    Event.EVAL_PROCESS_PARSE_FAILED: frozenset({"failure_type", "mode"}),
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


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping_value(mapping: Mapping[str, object], key: str) -> str | None:
    return _string_value(mapping.get(key))


def _onboard_completed_properties(config: Mapping[str, object]) -> Properties:
    properties: Properties = {}

    wizard_obj = config.get("wizard")
    if isinstance(wizard_obj, Mapping):
        wizard_mode = _mapping_value(wizard_obj, "mode")
        configured_target = _mapping_value(wizard_obj, "configured_target")
        if wizard_mode is not None:
            properties["wizard_mode"] = wizard_mode
        if configured_target is not None:
            properties["configured_target"] = configured_target

    targets_obj = config.get("targets")
    if isinstance(targets_obj, Mapping):
        local_obj = targets_obj.get("local")
        if isinstance(local_obj, Mapping):
            provider = _mapping_value(local_obj, "provider")
            model = _mapping_value(local_obj, "model")
            if provider is not None:
                properties["provider"] = provider
            if model is not None:
                properties["model"] = model

    return properties


def _investigation_started_properties(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    evaluate_requested: bool,
) -> Properties:
    properties: Properties = {
        "has_input_file": input_path is not None,
        "has_inline_json": input_json is not None,
        "interactive": interactive,
        "evaluate_requested": evaluate_requested,
    }
    llm_provider = _string_value(os.getenv("LLM_PROVIDER"))
    llm_model = _string_value(os.getenv("ANTHROPIC_MODEL")) or _string_value(
        os.getenv("OPENAI_MODEL")
    )
    if llm_provider is not None:
        properties["llm_provider"] = llm_provider
    if llm_model is not None:
        properties["llm_model"] = llm_model
    return properties


def _capture(event: Event, properties: Properties | None = None) -> None:
    try:
        get_analytics().capture(event, properties)
    except Exception as exc:  # noqa: BLE001
        capture_exception(exc)


def _bucket_duration_ms(duration_ms: float) -> str:
    if duration_ms < 500:
        return "<500ms"
    if duration_ms < 1000:
        return "500ms-1s"
    if duration_ms < 3000:
        return "1s-3s"
    if duration_ms < 5000:
        return "3s-5s"
    return ">=5s"


def _bucket_score(score_0_100: int) -> str:
    if score_0_100 < 50:
        return "0-49"
    if score_0_100 < 70:
        return "50-69"
    if score_0_100 < 85:
        return "70-84"
    return "85-100"


def _bucket_percentage(percent: float) -> str:
    if percent < 25:
        return "0-24"
    if percent < 50:
        return "25-49"
    if percent < 75:
        return "50-74"
    if percent < 95:
        return "75-94"
    return "95-100"


def _bucket_rubric_length(text: str) -> str:
    size = len(text.strip())
    if size == 0:
        return "0"
    if size < 256:
        return "1-255"
    if size < 1024:
        return "256-1023"
    if size < 4096:
        return "1024-4095"
    return ">=4096"


def build_cli_invoked_properties(
    *,
    entrypoint: str,
    command_parts: list[str],
    json_output: bool = False,
    verbose: bool = False,
    debug: bool = False,
    yes: bool = False,
    interactive: bool = True,
) -> Properties:
    """Build a structured ``cli_invoked`` payload for any CLI surface.

    Used by ``opensre`` (Click-driven) and the ``python -m app.*`` entrypoints
    so all three end up with the same property names. Records command names
    only — never raw argv values, option values, paths, URLs, or secrets.
    """
    properties: Properties = {
        "entrypoint": entrypoint,
        "command_path": " ".join((entrypoint, *command_parts)),
        "command_family": command_parts[0] if command_parts else "root",
        "json_output": json_output,
        "verbose": verbose,
        "debug": debug,
        "yes": yes,
        "interactive": interactive,
    }
    if len(command_parts) > 1:
        properties["subcommand"] = command_parts[1]
    if command_parts:
        properties["command_leaf"] = command_parts[-1]
    return properties


def capture_cli_invoked(properties: Properties | None = None) -> None:
    _capture(Event.CLI_INVOKED, properties)


def capture_repl_execution_policy_decision(properties: Properties | None = None) -> None:
    _capture(Event.REPL_EXECUTION_POLICY_DECISION, properties)


def capture_onboard_started() -> None:
    _capture(Event.ONBOARD_STARTED)


def capture_onboard_completed(config: Mapping[str, object]) -> None:
    _capture(Event.ONBOARD_COMPLETED, _onboard_completed_properties(config))


def capture_onboard_failed() -> None:
    _capture(Event.ONBOARD_FAILED)


def capture_investigation_started(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    evaluate_requested: bool = False,
) -> None:
    _capture(
        Event.INVESTIGATION_STARTED,
        _investigation_started_properties(
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
            evaluate_requested=evaluate_requested,
        ),
    )


def capture_investigation_completed() -> None:
    _capture(Event.INVESTIGATION_COMPLETED)


def capture_investigation_failed() -> None:
    _capture(Event.INVESTIGATION_FAILED)


def capture_integration_setup_started(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_STARTED, {"service": service})


def capture_integration_setup_completed(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_COMPLETED, {"service": service})


def capture_integrations_listed() -> None:
    _capture(Event.INTEGRATIONS_LISTED)


def capture_integration_removed(service: str) -> None:
    _capture(Event.INTEGRATION_REMOVED, {"service": service})


def capture_integration_verified(service: str) -> None:
    _capture(Event.INTEGRATION_VERIFIED, {"service": service})


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


def capture_eval_process_started(*, rubric: str, mode: str) -> None:
    _capture(
        Event.EVAL_PROCESS_STARTED,
        {
            "rubric_present": bool(rubric.strip()),
            "rubric_length_bucket": _bucket_rubric_length(rubric),
            "mode": mode,
        },
    )


def capture_eval_process_skipped(*, reason: str, mode: str) -> None:
    _capture(
        Event.EVAL_PROCESS_SKIPPED,
        {
            "skip_reason": reason,
            "mode": mode,
        },
    )


def capture_eval_process_completed(
    *,
    duration_ms: float,
    overall_pass: bool,
    score_0_100: int,
    rubric_item_count: int,
    mode: str,
) -> None:
    _capture(
        Event.EVAL_PROCESS_COMPLETED,
        {
            "duration_ms": round(duration_ms, 2),
            "duration_bucket": _bucket_duration_ms(duration_ms),
            "overall_pass": overall_pass,
            "score_bucket": _bucket_score(score_0_100),
            "rubric_item_count": rubric_item_count,
            "mode": mode,
        },
    )


def capture_eval_process_parse_failed(*, failure_type: str, mode: str) -> None:
    _capture(
        Event.EVAL_PROCESS_PARSE_FAILED,
        {
            "failure_type": failure_type,
            "mode": mode,
        },
    )


def capture_eval_process_failed(
    *,
    duration_ms: float,
    failure_stage: str,
    failure_type: str,
    mode: str,
) -> None:
    _capture(
        Event.EVAL_PROCESS_FAILED,
        {
            "duration_ms": round(duration_ms, 2),
            "duration_bucket": _bucket_duration_ms(duration_ms),
            "failure_stage": failure_stage,
            "failure_type": failure_type,
            "mode": mode,
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


def capture_deploy_started(*, target: str, dry_run: bool) -> None:
    _capture(Event.DEPLOY_STARTED, {"target": target, "dry_run": dry_run})


def capture_deploy_completed(*, target: str, dry_run: bool) -> None:
    _capture(Event.DEPLOY_COMPLETED, {"target": target, "dry_run": dry_run})


def capture_deploy_failed(*, target: str, dry_run: bool) -> None:
    _capture(Event.DEPLOY_FAILED, {"target": target, "dry_run": dry_run})


def capture_update_started(*, check_only: bool) -> None:
    _capture(Event.UPDATE_STARTED, {"check_only": check_only})


def capture_update_completed(*, check_only: bool, updated: bool) -> None:
    _capture(Event.UPDATE_COMPLETED, {"check_only": check_only, "updated": updated})


def capture_update_failed(*, check_only: bool, reason: str) -> None:
    _capture(Event.UPDATE_FAILED, {"check_only": check_only, "reason": reason})
