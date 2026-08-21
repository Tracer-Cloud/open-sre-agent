from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import infrastructure.harness_ports as harness_ports
from core.agent_harness.turns.gather_discovery_budget import is_live_metric_query_call
from core.llm.types import ToolCall
from core.state import InvestigationState
from integrations.harness_adapters import register_harness_adapters
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


class _FixtureDatadogClient:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def query_metrics(
        self,
        query: str,
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        self.queries.append(
            {
                "query": query,
                "time_range_minutes": round((end - start).total_seconds() / 60),
            }
        )
        return {
            "success": True,
            "series": [
                {
                    "metric": "system.cpu.user",
                    "scope": "service:checkout-api,env:prod",
                    "expression": query,
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
    client = _FixtureDatadogClient()
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
                "api_key": "datadog-api-key",
                "app_key": "datadog-app-key",
                "metric_name": "system.cpu.user",
                "metric_query": query,
                "time_range_minutes": 15,
            }
        },
    }

    try:
        register_harness_adapters()
        with (
            patch("tools.investigation.stages.gather_evidence.agent.get_tracker", MagicMock),
            patch("integrations.datadog.tools.make_client", return_value=client),
        ):
            result = ConnectedInvestigationAgent(llm_factory=lambda: mock_llm).run(state)

        assert is_live_metric_query_call("query_datadog_metrics", {}) is True
        assert client.queries == [
            {
                "query": query,
                "time_range_minutes": 15,
            }
        ]
        assert any(message.get("content") == diagnosis for message in result["agent_messages"])
    finally:
        harness_ports.reset_harness_ports()
