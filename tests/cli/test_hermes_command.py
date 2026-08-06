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

import contextlib
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from surfaces.cli.commands.hermes import hermes_command


@pytest.fixture
def telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "12345")


@pytest.fixture
def rocketchat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate the local integration store so this test is hermetic even on a
    # machine with real Rocket.Chat credentials configured (e.g. this repo's
    # own dev setup).
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", lambda: {})
    monkeypatch.setenv("ROCKETCHAT_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("ROCKETCHAT_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("ROCKETCHAT_USER_ID", "u1")
    monkeypatch.setenv("ROCKETCHAT_DEFAULT_CHANNEL", "#incidents")


class _AgentBuildAbort(Exception):
    """Raised after capturing the constructed sink to abort the watch loop."""

    def __init__(self, sink: object) -> None:
        self.sink = sink


def _patch_agent_to_capture_sink():
    """Patch HermesAgent.__init__ to capture the sink and abort."""

    def _capture(self, *, sink, log_path, from_start):  # type: ignore[no-untyped-def]
        raise _AgentBuildAbort(sink)

    return patch("surfaces.cli.commands.hermes.HermesAgent.__init__", _capture)


def test_bare_hermes_prints_help_and_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(hermes_command, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "watch" in result.output


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

    from integrations.hermes.correlating_sink import CorrelatingSink

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

    from integrations.hermes.sinks import TelegramSink

    assert isinstance(captured.get("sink"), TelegramSink)


def test_watch_help_shows_provider_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(hermes_command, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--provider" in result.output


def test_watch_defaults_to_telegram_dispatcher(telegram_env: None) -> None:
    captured: dict[str, object] = {}

    with _patch_agent_to_capture_sink():
        runner = CliRunner()
        try:
            runner.invoke(hermes_command, ["watch", "--no-correlate"], catch_exceptions=False)
        except _AgentBuildAbort as exc:
            captured["sink"] = exc.sink

    from integrations.telegram.alarms import AlarmDispatcher

    sink = captured["sink"]
    assert isinstance(sink._dispatcher, AlarmDispatcher)  # type: ignore[attr-defined]


def test_watch_rocketchat_provider_builds_rocketchat_dispatcher(
    rocketchat_env: None,
) -> None:
    captured: dict[str, object] = {}

    with _patch_agent_to_capture_sink():
        runner = CliRunner()
        try:
            runner.invoke(
                hermes_command,
                ["watch", "--no-correlate", "--provider", "rocketchat"],
                catch_exceptions=False,
            )
        except _AgentBuildAbort as exc:
            captured["sink"] = exc.sink

    from integrations.rocketchat.alarms import RocketChatAlarmDispatcher

    sink = captured["sink"]
    assert isinstance(sink._dispatcher, RocketChatAlarmDispatcher)  # type: ignore[attr-defined]


def test_watch_rocketchat_provider_passes_chat_id_as_channel_override(
    rocketchat_env: None,
) -> None:
    captured: dict[str, object] = {}

    def _fake_load(**kwargs: object) -> object:
        captured.update(kwargs)
        from integrations.rocketchat.credentials import RocketChatCredentials

        return RocketChatCredentials(
            server_url="https://chat.example.com",
            auth_token="tok",
            user_id="u1",
            channel="#ops",
        )

    with (
        patch(
            "surfaces.cli.commands.hermes.load_rocketchat_credentials_from_env",
            _fake_load,
        ),
        _patch_agent_to_capture_sink(),
        contextlib.suppress(_AgentBuildAbort),
    ):
        runner = CliRunner()
        runner.invoke(
            hermes_command,
            ["watch", "--no-correlate", "--provider", "rocketchat", "--chat-id", "#ops"],
            catch_exceptions=False,
        )

    assert captured == {"channel_override": "#ops"}


def test_watch_rejects_invalid_provider() -> None:
    runner = CliRunner()
    result = runner.invoke(hermes_command, ["watch", "--provider", "discord"])
    assert result.exit_code != 0


def test_watch_rejects_invalid_escalation_threshold(telegram_env: None) -> None:
    """The correlator's own validation should surface as a non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(
        hermes_command,
        ["watch", "--escalation-threshold", "1"],
        catch_exceptions=True,
    )
    assert result.exit_code != 0
