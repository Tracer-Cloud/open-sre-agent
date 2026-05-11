"""Tests for ``node_agent_incident``."""

from __future__ import annotations

from app.nodes.agent_incident.node import node_agent_incident
from app.state.factory import make_agent_incident_state


def test_agent_incident_returns_noise_without_payload() -> None:
    state = make_agent_incident_state(agent_name="x", breach_reason="y")
    state["context"] = {}
    out = node_agent_incident(state)
    assert out["is_noise"] is True
    assert "raw_alert" not in out


def test_agent_incident_returns_noise_when_required_fields_blank() -> None:
    state = make_agent_incident_state(agent_name="", breach_reason="stuck")
    out = node_agent_incident(state)
    assert out["is_noise"] is True

    state2 = make_agent_incident_state(agent_name="aider", breach_reason="   ")
    out2 = node_agent_incident(state2)
    assert out2["is_noise"] is True


def test_agent_incident_synthesizes_investigation_fields() -> None:
    state = make_agent_incident_state(
        agent_name="claude-code",
        breach_reason="no progress for 8m",
        pid=8421,
        stdout_tail="line1\nline2\n",
        resource_snapshot={"cpu_pct": 4.2},
    )
    out = node_agent_incident(state)
    assert out["is_noise"] is False
    assert out["alert_source"] == "agent_incident"
    assert "claude-code" in out["alert_name"]
    assert "8421" in out["alert_name"]
    assert out["pipeline_name"] == "local_agent_fleet"
    assert out["severity"] == "warning"
    assert isinstance(out["raw_alert"], dict)
    assert out["raw_alert"]["alert_source"] == "agent_incident"
    assert out["raw_alert"]["agent_incident"]["agent_name"] == "claude-code"
    assert "problem_md" in out and "Breach:" in out["problem_md"]
    assert isinstance(out["alert_json"], dict)
    assert out["alert_json"]["is_noise"] is False
    iw = out["incident_window"]
    assert isinstance(iw, dict)
    assert iw.get("_schema_version") == 1
    assert iw.get("source") == "default"
    assert "since" in iw and "until" in iw
