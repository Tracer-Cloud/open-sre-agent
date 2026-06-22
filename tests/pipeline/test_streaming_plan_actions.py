from __future__ import annotations

from typing import Any

import pytest

from app.agent.stages.investigate import ConnectedInvestigationAgent
from app.pipeline.runners import astream_investigation


def _agent_run_stub(
    _self: ConnectedInvestigationAgent,
    _state: dict[str, Any],
    on_event: Any | None = None,
) -> dict[str, Any]:
    if on_event is not None:
        on_event("agent_start", {})
        on_event("agent_end", {})
    return {"agent_messages": []}


@pytest.mark.asyncio
async def test_astream_investigation_emits_plan_actions_before_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent.stages.resolve_integrations.resolve_integrations",
        lambda _state: {},
    )
    monkeypatch.setattr(
        "app.agent.stages.extract_alert.extract_alert",
        lambda _state: {"alert_name": "test-alert", "is_noise": False},
    )
    monkeypatch.setattr(
        "app.agent.stages.plan_actions.plan_actions",
        lambda _state: {"planned_actions": ["query_logs"], "plan_rationale": "logs first"},
    )
    monkeypatch.setattr(
        ConnectedInvestigationAgent,
        "run",
        _agent_run_stub,
    )
    monkeypatch.setattr(
        "app.agent.stages.diagnose.diagnose",
        lambda _state: {"root_cause": "unknown", "validity_score": 0.0},
    )
    monkeypatch.setattr("app.agent.correlation.node.node_correlate_upstream", lambda *_a: {})
    monkeypatch.setattr(
        "app.agent.stages.publish_findings.node.generate_report",
        lambda _state: {"report": "done"},
    )

    events = [event async for event in astream_investigation("alert text")]
    chain_end_events = [
        event
        for event in events
        if event.node_name in {"plan_actions", "investigation_agent"}
        and event.kind == "on_chain_end"
    ]

    assert [event.node_name for event in chain_end_events[:2]] == [
        "plan_actions",
        "investigation_agent",
    ]
    plan_event = chain_end_events[0]
    output = plan_event.data["data"]["output"]
    assert output["planned_actions"] == ["query_logs"]
