from __future__ import annotations

import pytest

from platform.analytics import cli
from platform.analytics.events import Event
from platform.analytics.source import EntrypointSource, TriggerMode


def _assert_investigation_events_have_source(
    events: list[tuple[Event, dict[str, object] | None]],
) -> None:
    investigation_events = {
        Event.INVESTIGATION_STARTED,
        Event.INVESTIGATION_COMPLETED,
        Event.INVESTIGATION_FAILED,
        Event.INVESTIGATION_OUTCOME,
        Event.INVESTIGATION_CANCELLED,
    }
    for event, properties in events:
        if event not in investigation_events:
            continue
        assert properties is not None, f"{event.value} must include properties"
        source = properties.get("source")
        assert isinstance(source, str), f"{event.value} must include a string source"
        assert source.strip(), f"{event.value} source must be non-empty"
        assert properties.get("investigation_loop_count") is not None, (
            f"{event.value} must include investigation_loop_count"
        )
        assert properties.get("investigation_iteration_cap") is not None, (
            f"{event.value} must include investigation_iteration_cap"
        )


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object] | None]] = []
        self.identified: list[dict[str, object]] = []
        self.persistent_properties: dict[str, object] = {}

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, properties))

    def identify(self, set_properties: dict[str, object]) -> None:
        self.identified.append(set_properties)

    def set_persistent_property(self, key: str, value: object) -> None:
        self.persistent_properties[key] = value


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


def test_identify_github_username_sets_person_property(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.identify_github_username("octocat")

    assert stub.identified == [{"github_username": "octocat"}]
    assert stub.persistent_properties == {"github_username": "octocat"}


def test_identify_github_username_noop_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.identify_github_username("")

    assert stub.identified == []


def test_identify_saved_github_username_reads_store(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)
    monkeypatch.setattr(
        "integrations.github.identity.saved_github_username",
        lambda: "octocat",
    )

    cli.identify_saved_github_username()

    assert stub.identified == [{"github_username": "octocat"}]
    assert stub.persistent_properties == {"github_username": "octocat"}


def test_identify_saved_github_username_noop_when_store_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)
    monkeypatch.setattr("integrations.github.identity.saved_github_username", lambda: "")

    cli.identify_saved_github_username()

    assert stub.identified == []


def test_identify_github_username_reports_failures_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("analytics unavailable")

    def raise_error() -> _StubAnalytics:
        raise expected_error

    monkeypatch.setattr(cli, "get_analytics", raise_error)
    monkeypatch.setattr(cli, "capture_exception", captured_errors.append)

    cli.identify_github_username("octocat")

    assert captured_errors == [expected_error]


def test_capture_github_login_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_github_login_completed("octocat")

    assert stub.events == [
        (Event.GITHUB_LOGIN_COMPLETED, {"github_username": "octocat"}),
    ]


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


def test_track_investigation_emits_lifecycle_once(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    with cli.track_investigation(
        entrypoint=EntrypointSource.CLI_COMMAND,
        trigger_mode=TriggerMode.FILE,
        input_path="alert.json",
    ):
        pass

    emitted_events = [event for event, _ in stub.events]
    assert emitted_events == [Event.INVESTIGATION_STARTED, Event.INVESTIGATION_COMPLETED]
    _assert_investigation_events_have_source(stub.events)
    started_props = stub.events[0][1] or {}
    completed_props = stub.events[1][1] or {}
    assert started_props["source"] == "test"
    assert started_props["entrypoint_source"] == "cli_command"
    assert started_props["category"] == "test"
    assert started_props["trigger_mode"] == "file"
    assert started_props["is_test"] is True
    assert started_props["investigation_id"] == completed_props["investigation_id"]
    assert started_props["investigation_loop_count"] == 0
    assert completed_props["investigation_loop_count"] == 0


def test_track_investigation_emits_failed_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    # Wrap the raise in a callable so the ``raise`` lives inside ``_trigger``
    # rather than directly in the test body. ``pytest.raises`` then sees a
    # plain function call as its protected expression, which lets CodeQL
    # ``py/unreachable-statement`` prove the assertions below are reachable
    # (the previous nested-``with`` workaround still tripped the rule).
    def _trigger() -> None:
        with cli.track_investigation(
            entrypoint=EntrypointSource.MCP,
            trigger_mode=TriggerMode.SERVICE_RUNTIME,
        ):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _trigger()

    emitted_events = [event for event, _ in stub.events]
    assert emitted_events == [Event.INVESTIGATION_STARTED, Event.INVESTIGATION_FAILED]
    _assert_investigation_events_have_source(stub.events)
    failed_props = stub.events[1][1] or {}
    assert failed_props["failure_type"] == "RuntimeError"
    assert failed_props["failure_message"] == "boom"


def test_capture_investigation_failed_includes_state_loop_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_investigation_failed(
        failure_type="RuntimeError",
        failure_message="boom",
        shared_properties={"investigation_id": "inv-fail"},
        state={"investigation_loop_count": 4, "investigation_iteration_cap": 20},
    )

    failed_props = stub.events[0][1] or {}
    assert failed_props["investigation_loop_count"] == 4
    assert failed_props["investigation_iteration_cap"] == 20


def test_track_investigation_failed_uses_tracker_loop_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    def _trigger() -> None:
        with cli.track_investigation(
            entrypoint=EntrypointSource.CLI_COMMAND,
            trigger_mode=TriggerMode.FILE,
        ) as tracker:
            tracker.record_loop_metrics_from_state(
                {"investigation_loop_count": 6, "investigation_iteration_cap": 20}
            )
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _trigger()

    failed_props = stub.events[1][1] or {}
    assert failed_props["investigation_loop_count"] == 6
    assert failed_props["investigation_iteration_cap"] == 20


def test_capture_investigation_outcome_and_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_investigation_outcome(
        investigation_id="inv-123",
        status="failed",
        investigation_target="generic",
        error_excerpt="boom",
        failure_category="unknown",
        state={"investigation_loop_count": 5, "investigation_iteration_cap": 20},
    )
    cli.capture_investigation_cancelled(
        investigation_id="inv-456",
        investigation_target="alert.json",
        state={"investigation_loop_count": 2, "investigation_iteration_cap": 20},
    )

    assert stub.events[0][0] == Event.INVESTIGATION_OUTCOME
    outcome_props = stub.events[0][1] or {}
    assert outcome_props["investigation_id"] == "inv-123"
    assert outcome_props["status"] == "failed"
    assert outcome_props["investigation_target"] == "generic"
    assert outcome_props["error_excerpt"] == "boom"
    assert outcome_props["investigation_loop_count"] == 5
    assert stub.events[1][0] == Event.INVESTIGATION_CANCELLED
    cancelled_props = stub.events[1][1] or {}
    assert cancelled_props["investigation_id"] == "inv-456"
    assert cancelled_props["failure_category"] == "user_cancelled"
    assert cancelled_props["investigation_loop_count"] == 2


def test_track_investigation_records_loop_metrics_on_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    with cli.track_investigation(
        entrypoint=EntrypointSource.CLI_COMMAND,
        trigger_mode=TriggerMode.FILE,
    ) as tracker:
        tracker.record_loop_metrics_from_state(
            {"investigation_loop_count": 8, "investigation_iteration_cap": 20}
        )

    completed_props = stub.events[1][1] or {}
    assert completed_props["investigation_loop_count"] == 8
    assert completed_props["investigation_iteration_cap"] == 20


def test_track_investigation_nested_context_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    with (
        cli.track_investigation(
            entrypoint=EntrypointSource.SDK,
            trigger_mode=TriggerMode.SERVICE_RUNTIME,
        ),
        cli.track_investigation(
            entrypoint=EntrypointSource.CLI_COMMAND,
            trigger_mode=TriggerMode.FILE,
        ),
    ):
        pass

    emitted_events = [event for event, _ in stub.events]
    assert emitted_events == [Event.INVESTIGATION_STARTED, Event.INVESTIGATION_COMPLETED]
    _assert_investigation_events_have_source(stub.events)


def test_capture_diagnosis_category_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_diagnosis_category_mismatch(
        root_cause_category="dns_resolution_failure",
        mismatch_reason="root cause text signals database (2 keyword hits)",
    )

    assert stub.events == [
        (
            Event.DIAGNOSIS_CATEGORY_MISMATCH,
            {
                "category_text_mismatch": True,
                "root_cause_category": "dns_resolution_failure",
                "mismatch_reason": "root cause text signals database (2 keyword hits)",
            },
        )
    ]
