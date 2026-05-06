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
