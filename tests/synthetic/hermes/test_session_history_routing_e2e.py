"""End-to-end routing proof for the stock Hermes session-history warning."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.hermes.agent import HermesAgent
from integrations.hermes.classifier import IncidentClassifier
from integrations.hermes.correlating_sink import CorrelatingSink
from integrations.hermes.correlator import IncidentCorrelator, RouteDestination
from integrations.hermes.incident import HermesIncident, IncidentSeverity

pytestmark = pytest.mark.synthetic

_SCENARIO_DIR = Path(__file__).parent / "011-session-history-unavailable"


def test_stock_session_history_warning_reaches_telegram_route() -> None:
    log_path = _SCENARIO_DIR / "errors.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    delivered: list[HermesIncident] = []
    sink = CorrelatingSink(
        correlator=IncidentCorrelator(),
        routes={RouteDestination.TELEGRAM: delivered.append},
    )
    agent = HermesAgent(
        sink=sink,
        log_path=log_path,
        classifier=IncidentClassifier(),
    )

    emitted = agent.process(lines)

    assert delivered == emitted
    assert len(delivered) == 1
    incident = delivered[0]
    assert incident.rule == "session_history_unavailable"
    assert incident.severity is IncidentSeverity.MEDIUM
    assert incident.logger == "gateway.platforms.api_server"
    assert incident.records[0].raw == lines[0]
    assert sink.metrics_snapshot()["delivered"] == 1


def test_repeated_stock_warning_deduplicates_then_escalates_on_telegram() -> None:
    log_path = _SCENARIO_DIR / "errors.log"
    source_line = log_path.read_text(encoding="utf-8").strip()
    lines = [
        source_line.replace("14:03:12,150", f"14:03:{second:02d},150").replace(
            "session_opaque", f"session_{second}"
        )
        for second in (12, 13, 14)
    ]

    delivered: list[HermesIncident] = []
    sink = CorrelatingSink(
        correlator=IncidentCorrelator(),
        routes={RouteDestination.TELEGRAM: delivered.append},
    )
    agent = HermesAgent(
        sink=sink,
        log_path=log_path,
        classifier=IncidentClassifier(),
    )

    emitted = agent.process(lines)

    assert len(emitted) == 3
    assert [incident.severity for incident in delivered] == [
        IncidentSeverity.MEDIUM,
        IncidentSeverity.HIGH,
    ]
    assert all(incident.rule == "session_history_unavailable" for incident in delivered)
    assert delivered[1].fingerprint.endswith(":escalated")
    assert sink.metrics_snapshot() == {
        "delivered": 2,
        "suppressed": 1,
        "escalated": 1,
        "dropped": 0,
        "unrouted": 0,
        "sink_errors": 0,
    }
