from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import platform.harness_ports as harness_ports
from core.agent_harness.turns.gather_discovery_budget import is_live_metric_query_call
from core.llm.types import ToolCall
from core.state import InvestigationState
from integrations.harness_adapters import register_harness_adapters
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


class _FixtureDatadogBackend:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def query_metrics(self, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(kwargs)
        metric_name = str(kwargs["metric_name"])
        return {
            "source": "datadog_metrics",
            "available": True,
            "metric_name": metric_name,
            "metrics": [
                {
                    "metric": metric_name,
                    "scope": "service:checkout-api,env:prod",
                    "expression": f"avg:{metric_name}{{service:checkout-api,env:prod}}",
                    "points": [
                        {"timestamp": "2026-08-17T09:30:00Z", "value": 0.91},
                        {"timestamp": "2026-08-17T09:31:00Z", "value": 0.96},
                    ],
                }
            ],
        }


def _tool_call_response(tool_call: ToolCall) -> MagicMock:
    response = MagicMock()
    response.tool_calls = [tool_call]
    response.has_tool_calls = True
    response.content = ""
    response.raw_content = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.input),
                },
            }
        ],
    }
    return response


def _text_response(text: str) -> MagicMock:
    response = MagicMock()
    response.tool_calls = []
    response.has_tool_calls = False
    response.content = text
    response.raw_content = {"role": "assistant", "content": text}
    return response


def test_datadog_metric_fetch_is_live_investigation_evidence() -> None:
    harness_ports.reset_harness_ports()
    backend = _FixtureDatadogBackend()
    query = "avg:system.cpu.user{service:checkout-api,env:prod}"
    diagnosis = (
        "Triage complete: checkout CPU saturation confirmed.\n"
        "Status - confirmed: CPU saturation | open: none | next: scale service | owner: on-call\n"
        "Hypotheses:\n"
        "1. Checkout CPU saturation - confirm: Datadog metrics; rule out: idle capacity\n"
        "Verification:\n"
        "1. Datadog metrics (H1): CPU rose from 0.91 to 0.96\n"
        "Follow-up questions:\n"
        "1. Did traffic increase before the alert?\n"
        "Remediation trade-offs: scale now, then profile the hot path"
    )
    mock_llm = MagicMock()
    mock_llm._model = "gpt-4o"
    mock_llm.invoke.side_effect = [
        _tool_call_response(
            ToolCall(
                id="datadog-metrics-1",
                name="query_datadog_metrics",
                input={"metric_name": "system.cpu.user", "query": query},
            )
        ),
        _text_response(diagnosis),
    ]
    mock_llm.build_tool_result_message.side_effect = lambda _calls, results: {
        "role": "user",
        "content": json.dumps(results, default=str),
    }
    state: InvestigationState = {
        "alert_name": "Checkout CPU saturation",
        "severity": "critical",
        "resolved_integrations": {
            "datadog": {
                "connection_verified": True,
                "metric_name": "system.cpu.user",
                "metric_query": query,
                "time_range_minutes": 15,
                "_backend": backend,
            }
        },
    }

    try:
        with (
            patch(
                "tools.investigation.stages.gather_evidence.agent.get_llm",
                return_value=mock_llm,
            ),
            patch(
                "tools.investigation.stages.gather_evidence.agent.get_tracker",
                return_value=MagicMock(),
            ),
        ):
            register_harness_adapters()
            result = ConnectedInvestigationAgent().run(state)

        assert is_live_metric_query_call("query_datadog_metrics", {}) is True
        assert backend.queries == [
            {
                "metric_name": "system.cpu.user",
                "time_range_minutes": 15,
                "query": query,
            }
        ]
        assert any(message.get("content") == diagnosis for message in result["agent_messages"])
    finally:
        harness_ports.reset_harness_ports()
