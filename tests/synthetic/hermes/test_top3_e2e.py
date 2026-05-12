"""End-to-end tests for the top-3 Hermes operational issues.

These exercise the **full** runtime stack — ``FileTailer`` polling a
real file on disk, the classifier reacting to lines as they're
appended, and the ``TelegramSink`` formatting + dispatching via a
patched ``AlarmDispatcher``. Unlike ``test_suite.py`` (which feeds
lines to the classifier directly) and ``test_telegram_dispatch.py``
(which uses ``agent.process`` for synchronous classification), this
suite drives the daemon thread through ``HermesAgent.start()`` and
asserts behaviour against the live tailer.

The three scenarios covered here correspond to the top operational
issues surfaced in the Hermes Agent issue tracker:

1. ``001-gateway-auth-bypass-after-restart`` (#23778, P0 security)
2. ``002-gateway-systemd-crash-loop`` (gateway troubleshooting)
3. ``003-state-db-wal-unbounded-growth`` (#24034)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from app.hermes.agent import HermesAgent
from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident
from app.hermes.sinks import TelegramSink
from app.watch_dog.alarms import AlarmCredentials, AlarmDispatcher
from tests.synthetic.hermes.scenario_loader import (
    SUITE_DIR,
    HermesScenarioFixture,
    load_scenario,
)

pytestmark = [pytest.mark.synthetic, pytest.mark.e2e]

# Generous default — these tests stream lines through a daemon thread
# polling at 0.05s and we wait for the classifier+sink to catch up.
_WAIT_BUDGET_S = 5.0
_POLL_INTERVAL_S = 0.05


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(chat_id: str, text: str, bot_token: str) -> tuple[bool, str, str]:
        calls.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return True, "", "1"

    monkeypatch.setattr("app.watch_dog.alarms.post_telegram_message", _fake_post)
    return calls


def _build_agent(
    log_path: Path,
    fixture: HermesScenarioFixture,
    sink: Any,
) -> HermesAgent:
    classifier = IncidentClassifier(
        warning_burst_threshold=fixture.metadata.classifier.warning_burst_threshold,
        warning_burst_window_s=fixture.metadata.classifier.warning_burst_window_s,
        traceback_followup_s=fixture.metadata.classifier.traceback_followup_s,
    )
    return HermesAgent(
        sink=sink,
        log_path=log_path,
        classifier=classifier,
        poll_interval_s=_POLL_INTERVAL_S,
        from_start=False,
    )


def _wait_for(
    predicate: Any,
    *,
    budget_s: float = _WAIT_BUDGET_S,
    poll_s: float = 0.02,
) -> bool:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return predicate()


def _drive_log(
    tmp_path: Path,
    fixture: HermesScenarioFixture,
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_incident_min: int,
) -> tuple[list[HermesIncident], list[dict[str, Any]]]:
    """Boot HermesAgent + TelegramSink against a temp file, write the
    fixture's log lines line-by-line, and wait for incidents to surface.
    """
    log_path = tmp_path / "errors.log"
    log_path.touch()

    telegram_calls = _patch_telegram(monkeypatch)
    creds = AlarmCredentials(bot_token="tok-e2e", chat_id="chat-e2e")
    dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
    sink = TelegramSink(dispatcher)

    incidents: list[HermesIncident] = []

    def _tee(incident: HermesIncident) -> None:
        incidents.append(incident)
        sink(incident)

    agent = _build_agent(log_path, fixture, _tee)
    agent.start()
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            for line in fixture.log_lines:
                handle.write(line + "\n")
                handle.flush()
                # Short stagger so the tailer's polling loop has a
                # chance to react between writes — this mimics real-time
                # log production without making the test slow.
                time.sleep(0.01)

        _wait_for(lambda: len(incidents) >= expected_incident_min)
    finally:
        agent.stop()

    return incidents, telegram_calls


# ---------------------------------------------------------------------
# Scenario 001 — gateway auth bypass after polling-conflict restart


def test_e2e_001_gateway_auth_bypass_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_scenario(SUITE_DIR / "001-gateway-auth-bypass-after-restart")

    incidents, calls = _drive_log(
        tmp_path,
        fixture,
        monkeypatch,
        expected_incident_min=2,  # warning_burst + error_severity
    )

    rules = [incident.rule for incident in incidents]
    assert "warning_burst" in rules, f"expected polling-conflict warning_burst, got rules={rules}"
    assert "error_severity" in rules, f"expected gateway.auth error_severity, got rules={rules}"

    # The auth bypass ERROR must reach Telegram — this is the P0 alert.
    # Issue #23778 specifies the bypass surfaces via `gateway.auth` with
    # the phrase "auth bypass" in the message body.
    auth_alerts = [
        call for call in calls if "gateway.auth" in call["text"] and "auth bypass" in call["text"]
    ]
    assert auth_alerts, (
        "P0 auth-bypass ERROR was not delivered to Telegram; "
        f"got {len(calls)} call(s): {[c['text'][:80] for c in calls]}"
    )

    # The polling-conflict burst must also reach Telegram. Per the
    # AlarmDispatcher contract, distinct fingerprints bypass cooldown so
    # both incidents land — verified by counting calls with each
    # fingerprint distinctly present in the message body.
    burst_alerts = [
        call
        for call in calls
        if "warning_burst" in call["text"] and "polling conflict" in call["text"]
    ]
    assert burst_alerts, (
        "polling-conflict warning_burst alert was not delivered to Telegram; "
        f"got {len(calls)} call(s)"
    )


# ---------------------------------------------------------------------
# Scenario 002 — systemd crash loop


def test_e2e_002_gateway_systemd_crash_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_scenario(SUITE_DIR / "002-gateway-systemd-crash-loop")

    incidents, calls = _drive_log(
        tmp_path,
        fixture,
        monkeypatch,
        expected_incident_min=2,  # traceback + at least one error_severity
    )

    rules = [incident.rule for incident in incidents]
    assert "traceback" in rules, f"expected traceback incident, got rules={rules}"

    critical_errors = [
        incident
        for incident in incidents
        if incident.rule == "error_severity" and incident.severity.value == "critical"
    ]
    assert critical_errors, (
        f"expected at least one CRITICAL error_severity for the crash-loop "
        f"exits, got severities={[i.severity.value for i in incidents]}"
    )

    # All four CRITICAL exit lines share the same logger+message text so
    # their `error_severity` fingerprints collapse via cooldown to one
    # send. The new `crash_loop` repeat-rule additionally fires once on
    # the same lines, producing a second (distinct) incident type. So we
    # expect exactly **two** Telegram sends mentioning the exit text:
    # one `error_severity` and one `crash_loop`.
    crashloop_calls = [c for c in calls if "Gateway process exited" in c["text"]]
    assert len(crashloop_calls) == 2, (
        "expected one error_severity (cooldown-collapsed) plus one crash_loop "
        f"incident for the crash-loop exits; got {len(crashloop_calls)}"
    )
    incident_rules = {i.rule for i in incidents}
    assert "crash_loop" in incident_rules, (
        f"crash_loop repeat-rule should have fired; rules={incident_rules}"
    )

    # The traceback Telegram alert must carry the actionable
    # `ModuleNotFoundError` line — that's the substring an operator
    # greps for to find the fix.
    traceback_alerts = [c for c in calls if "ModuleNotFoundError" in c["text"]]
    assert traceback_alerts, (
        "traceback alert did not include the actionable ModuleNotFoundError "
        f"line; got call texts: {[c['text'][:160] for c in calls]}"
    )


# ---------------------------------------------------------------------
# Scenario 003 — state.db WAL unbounded growth


def test_e2e_003_state_db_wal_unbounded_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_scenario(SUITE_DIR / "003-state-db-wal-unbounded-growth")

    incidents, calls = _drive_log(
        tmp_path,
        fixture,
        monkeypatch,
        expected_incident_min=2,  # burst + at least one ERROR
    )

    rules = [incident.rule for incident in incidents]
    assert "warning_burst" in rules, (
        f"WAL-growth warning_burst (early-warning) did not fire; rules={rules}"
    )
    assert "error_severity" in rules, (
        f"disk-full ERROR did not surface as error_severity; rules={rules}"
    )

    # Both the early-warning burst and the disk-full ERROR must reach
    # Telegram — they have different fingerprints so the cooldown does
    # not suppress one for the other.
    fingerprints_in_calls: set[str] = set()
    for incident in incidents:
        for call in calls:
            if incident.fingerprint in call["text"]:
                fingerprints_in_calls.add(incident.fingerprint)
    assert len(fingerprints_in_calls) >= 2, (
        "expected at least two distinct fingerprints delivered to "
        f"Telegram (burst + ERROR); got {fingerprints_in_calls}"
    )

    # The exact SQLite error string from issue #24034 must reach the
    # operator chat — that's the line that points at the real fix
    # (`PRAGMA wal_checkpoint(TRUNCATE)` instead of PASSIVE).
    disk_full_alerts = [c for c in calls if "database or disk is full" in c["text"]]
    assert disk_full_alerts, (
        "expected the real SQLite OperationalError string from #24034 in "
        f"a Telegram alert; got {len(calls)} call(s)"
    )
