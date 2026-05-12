"""Integration tests for ``opensre hermes watch`` CLI wiring.

These tests assert the command parses cleanly and the correlator
wiring is selectable. The watcher's actual tail loop is covered by
``tests/hermes/test_agent.py``.

We intercept ``HermesAgent.__init__`` to capture the sink the CLI
builds and raise immediately, short-circuiting the long-blocking
``stop_event.wait()`` path. ``signal.signal`` would otherwise refuse
to install handlers from a worker thread.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.cli.commands.hermes import hermes_command


@pytest.fixture
def telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "12345")


class _AgentBuildAbort(Exception):
    """Raised after capturing the constructed sink to abort the watch loop."""

    def __init__(self, sink: object) -> None:
        self.sink = sink


def _patch_agent_to_capture_sink():
    """Patch HermesAgent.__init__ to capture the sink and abort."""

    def _capture(self, *, sink, log_path, from_start):  # type: ignore[no-untyped-def]
        raise _AgentBuildAbort(sink)

    return patch("app.cli.commands.hermes.HermesAgent.__init__", _capture)


def test_watch_help_shows_correlator_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(hermes_command, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--correlate" in result.output
    assert "--no-correlate" in result.output
    assert "--dedup-window-seconds" in result.output
    assert "--escalation-threshold" in result.output
    assert "--escalation-window-seconds" in result.output


def test_watch_starts_with_correlator_by_default(telegram_env: None) -> None:
    """Default invocation should wire the CorrelatingSink."""
    captured: dict[str, object] = {}

    with _patch_agent_to_capture_sink():
        runner = CliRunner()
        try:
            runner.invoke(
                hermes_command,
                ["watch"],
                catch_exceptions=False,
            )
        except _AgentBuildAbort as exc:
            captured["sink"] = exc.sink

    from app.hermes.correlating_sink import CorrelatingSink

    assert isinstance(captured.get("sink"), CorrelatingSink)


def test_watch_no_correlate_uses_raw_telegram_sink(telegram_env: None) -> None:
    captured: dict[str, object] = {}

    with _patch_agent_to_capture_sink():
        runner = CliRunner()
        try:
            runner.invoke(
                hermes_command,
                ["watch", "--no-correlate"],
                catch_exceptions=False,
            )
        except _AgentBuildAbort as exc:
            captured["sink"] = exc.sink

    from app.hermes.sinks import TelegramSink

    assert isinstance(captured.get("sink"), TelegramSink)


def test_watch_rejects_invalid_escalation_threshold(telegram_env: None) -> None:
    """The correlator's own validation should surface as a non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(
        hermes_command,
        ["watch", "--escalation-threshold", "1"],
        catch_exceptions=True,
    )
    assert result.exit_code != 0
