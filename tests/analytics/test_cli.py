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
