from __future__ import annotations

import pytest

from app.analytics import cli
from app.analytics.events import Event


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object] | None]] = []

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, properties))


def test_capture_cli_invoked_uses_safe_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_cli_invoked({"command_path": "opensre version"})

    assert stub.events == [
        (Event.CLI_INVOKED, {"command_path": "opensre version"}),
    ]


def test_capture_cli_invoked_reports_analytics_failures_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("analytics unavailable")

    def raise_error() -> _StubAnalytics:
        raise expected_error

    monkeypatch.setattr(cli, "get_analytics", raise_error)
    monkeypatch.setattr(cli, "capture_exception", captured_errors.append)

    cli.capture_cli_invoked()

    assert captured_errors == [expected_error]


def test_build_cli_invoked_properties_includes_full_command_path() -> None:
    properties = cli.build_cli_invoked_properties(
        entrypoint="opensre",
        command_parts=["remote", "ops", "status"],
        debug=True,
    )

    assert properties == {
        "entrypoint": "opensre",
        "command_path": "opensre remote ops status",
        "command_family": "remote",
        "json_output": False,
        "verbose": False,
        "debug": True,
        "yes": False,
        "interactive": True,
        "subcommand": "ops",
        "command_leaf": "status",
    }


def test_build_cli_invoked_properties_handles_root_invocation() -> None:
    properties = cli.build_cli_invoked_properties(
        entrypoint="opensre",
        command_parts=[],
    )

    assert properties["command_path"] == "opensre"
    assert properties["command_family"] == "root"
    assert "subcommand" not in properties
    assert "command_leaf" not in properties


def test_capture_update_helpers_emit_expected_events(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_update_started(check_only=True)
    cli.capture_update_completed(check_only=False, updated=True)
    cli.capture_update_failed(check_only=False, reason="RuntimeError")

    assert stub.events == [
        (Event.UPDATE_STARTED, {"check_only": True}),
        (Event.UPDATE_COMPLETED, {"check_only": False, "updated": True}),
        (Event.UPDATE_FAILED, {"check_only": False, "reason": "RuntimeError"}),
    ]


def test_capture_eval_process_metrics_emit_expected_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_eval_process_started(rubric="must cite logs", mode="opensre_llm_judge")
    cli.capture_eval_process_completed(
        duration_ms=740.3,
        overall_pass=True,
        score_0_100=92,
        rubric_item_count=4,
        mode="opensre_llm_judge",
    )
    cli.capture_eval_process_parse_failed(
        failure_type="ValueError",
        mode="opensre_llm_judge",
    )
    cli.capture_eval_process_failed(
        duration_ms=1200.0,
        failure_stage="invoke_judge",
        failure_type="RuntimeError",
        mode="opensre_llm_judge",
    )
    cli.capture_eval_process_skipped(reason="missing_rubric", mode="opensre_llm_judge")

    for event, properties in stub.events:
        assert properties is not None
        required = cli.EVAL_AND_TERMINAL_EVENT_CONTRACT.get(event)
        if required is None:
            continue
        assert required.issubset(properties.keys())


def test_capture_terminal_metrics_emit_expected_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_terminal_actions_planned(planned_count=3, has_unhandled_clause=True)
    cli.capture_terminal_actions_executed(
        planned_count=3,
        executed_count=2,
        executed_success_count=1,
    )
    cli.capture_terminal_turn_summarized(
        planned_count=3,
        executed_count=2,
        executed_success_count=1,
        fallback_to_llm=True,
        session_turn_index=8,
        session_fallback_count=3,
        session_action_success_percent=75.0,
        session_fallback_rate_percent=37.5,
    )

    for event, properties in stub.events:
        assert properties is not None
        required = cli.EVAL_AND_TERMINAL_EVENT_CONTRACT.get(event)
        if required is None:
            continue
        assert required.issubset(properties.keys())


def test_eval_and_terminal_kpi_queries_cover_core_metrics() -> None:
    expected_keys = {
        "eval_pass_rate",
        "eval_latency_p50_p95_ms",
        "eval_parse_error_rate",
        "terminal_action_execution_success_rate",
        "terminal_fallback_rate",
    }
    assert expected_keys.issubset(cli.EVAL_AND_TERMINAL_KPI_QUERIES.keys())
    for query in cli.EVAL_AND_TERMINAL_KPI_QUERIES.values():
        assert "FROM events" in query
