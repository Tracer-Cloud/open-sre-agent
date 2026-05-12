"""Synchronous-poll variant of the top-3 e2e suite.

Sibling to :mod:`test_top3_e2e`, which exercises the live ``HermesAgent``
daemon-thread path. This file exercises the **synchronous** path via
the shared :class:`HermesLogFixture` helper — the same primitive that
backs the agent-facing ``get_hermes_logs`` tool.

The point of having both is to catch regressions in either polling
mode without doubling test runtime. The two suites assert the same
classifier outcomes; they differ only in how lines are fed in
(daemon ``FileTailer`` vs. cursor-driven polls).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident
from app.hermes.sinks import TelegramSink, TelegramSinkConfig
from app.watch_dog.alarms import AlarmCredentials, AlarmDispatcher
from tests.synthetic.hermes.scenario_loader import (
    SUITE_DIR,
    HermesScenarioFixture,
    load_scenario,
)
from tests.utils.hermes_logs_helper import hermes_log_fixture

pytestmark = [pytest.mark.synthetic, pytest.mark.e2e]


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(chat_id: str, text: str, bot_token: str) -> tuple[bool, str, str]:
        calls.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return True, "", "1"

    monkeypatch.setattr("app.watch_dog.alarms.post_telegram_message", _fake_post)
    return calls


def _drive_scenario_via_helper(
    tmp_path: Path,
    fixture: HermesScenarioFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[HermesIncident], list[dict[str, Any]]]:
    """Drive a scenario through the synchronous poller and dispatch
    every emitted incident through TelegramSink. Returns the
    accumulated incidents + the Telegram call log."""
    telegram_calls = _patch_telegram(monkeypatch)
    creds = AlarmCredentials(bot_token="tok-helper", chat_id="chat-helper")
    dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
    # Inline-only bridge config — no executor needed for these tests
    # since no bridge is supplied at all.
    sink = TelegramSink(dispatcher, config=TelegramSinkConfig(bridge_run_inline=True))

    incidents: list[HermesIncident] = []
    with hermes_log_fixture(tmp_path) as log_fix:
        log_fix.classifier = IncidentClassifier(
            warning_burst_threshold=fixture.metadata.classifier.warning_burst_threshold,
            warning_burst_window_s=fixture.metadata.classifier.warning_burst_window_s,
            traceback_followup_s=fixture.metadata.classifier.traceback_followup_s,
        )
        log_fix.write_lines(fixture.log_lines)
        poll = log_fix.poll_once()
        for incident in poll.incidents:
            incidents.append(incident)
            sink(incident)
    return incidents, telegram_calls


# ---------------------------------------------------------------------
# Scenario 001 — gateway auth bypass after polling-conflict restart


def test_helper_001_gateway_auth_bypass_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_scenario(SUITE_DIR / "001-gateway-auth-bypass-after-restart")
    incidents, calls = _drive_scenario_via_helper(tmp_path, fixture, monkeypatch)

    rules = {i.rule for i in incidents}
    assert "warning_burst" in rules, rules
    assert "error_severity" in rules, rules

    # P0 auth-bypass alert must land in Telegram.
    auth_alerts = [c for c in calls if "gateway.auth" in c["text"] and "auth bypass" in c["text"]]
    assert auth_alerts, f"P0 auth-bypass alert missing; got {len(calls)} calls"


# ---------------------------------------------------------------------
# Scenario 002 — systemd crash loop


def test_helper_002_gateway_systemd_crash_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_scenario(SUITE_DIR / "002-gateway-systemd-crash-loop")
    incidents, calls = _drive_scenario_via_helper(tmp_path, fixture, monkeypatch)

    rules = [i.rule for i in incidents]
    assert "traceback" in rules, rules

    critical = [
        i for i in incidents if i.rule == "error_severity" and i.severity.value == "critical"
    ]
    assert critical, (
        f"expected CRITICAL error_severity, got severities={[i.severity.value for i in incidents]}"
    )

    # ModuleNotFoundError is the actionable line; it must reach Telegram
    # via the traceback incident's message.
    mnf_calls = [c for c in calls if "ModuleNotFoundError" in c["text"]]
    assert mnf_calls, (
        f"ModuleNotFoundError missing from Telegram alerts: {[c['text'][:120] for c in calls]}"
    )


# ---------------------------------------------------------------------
# Scenario 003 — state.db WAL unbounded growth


def test_helper_003_state_db_wal_unbounded_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_scenario(SUITE_DIR / "003-state-db-wal-unbounded-growth")
    incidents, calls = _drive_scenario_via_helper(tmp_path, fixture, monkeypatch)

    rules = {i.rule for i in incidents}
    assert "warning_burst" in rules, rules
    assert "error_severity" in rules, rules

    # Real SQLite error string from #24034 must reach Telegram.
    disk_full = [c for c in calls if "database or disk is full" in c["text"]]
    assert disk_full, f"disk-full alert missing; got {len(calls)} calls"
