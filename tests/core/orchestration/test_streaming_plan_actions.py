from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.orchestration.entrypoints import astream_investigation
from app.core.orchestration.node.investigate import ConnectedInvestigationAgent
from app.core.orchestration.stream_payloads import resolved_integrations_stream_payload
from app.integrations.config_models import RedisIntegrationConfig


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
        "app.core.orchestration.node.resolve_integrations.resolve_integrations",
        lambda _state: {"resolved_integrations": {}},
    )
    monkeypatch.setattr(
        "app.core.orchestration.node.extract_alert.extract_alert",
        lambda _state: {"alert_name": "test-alert", "is_noise": False},
    )
    monkeypatch.setattr(
        "app.core.orchestration.node.plan_actions.plan_actions",
        lambda _state: {"planned_actions": ["query_logs"], "plan_rationale": "logs first"},
    )
    monkeypatch.setattr(
        ConnectedInvestigationAgent,
        "run",
        _agent_run_stub,
    )
    monkeypatch.setattr(
        "app.core.orchestration.node.diagnose.diagnose",
        lambda _state: {"root_cause": "unknown", "validity_score": 0.0},
    )
    monkeypatch.setattr(
        "app.core.orchestration.node.publish_findings.upstream_correlation.node.node_correlate_upstream",
        lambda *_a: {},
    )
    monkeypatch.setattr(
        "app.core.orchestration.node.publish_findings.node.generate_report",
        lambda _state, **_kwargs: {"report": "done"},
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


def test_resolved_integrations_stream_payload_normalizes_multi_instance_configs() -> None:
    redis_config = RedisIntegrationConfig(host="cache.prod", port=6380, db=2)
    payload = resolved_integrations_stream_payload(
        {
            "redis": redis_config,
            "_all_redis_instances": [
                {
                    "name": "primary",
                    "tags": {"role": "primary"},
                    "config": redis_config,
                    "integration_id": "redis-primary",
                }
            ],
            "_all": [{"service": "redis"}],
        }
    )

    assert payload["redis"]["host"] == "cache.prod"
    assert payload["_all_redis_instances"][0]["config"]["db"] == 2
    assert isinstance(payload["_all_redis_instances"][0]["config"], dict)
    assert "_all" not in payload
    json.dumps({"resolved_integrations": payload})
