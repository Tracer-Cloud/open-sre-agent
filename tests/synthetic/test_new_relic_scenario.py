"""Synthetic RCA scenario using New Relic as the evidence source.

Unlike the file-per-vendor scenarios elsewhere in this directory (which call a
tool function directly), the scenario test below drives the *real*
investigation ReAct loop end to end (``ConnectedInvestigationAgent().run``)
against the *real* tool registry: only the LLM and the New Relic HTTP client
are mocked. This proves ``query_new_relic_alerts`` is discovered and invoked
through the normal runtime path — registry discovery, alert-source-driven
availability, and injected-credential merging — not by inspecting the
registry by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from core.domain.alerts.alert_source import alert_source_routing
from core.llm.types import ToolCall
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent
from tools.registry import get_registered_tools

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "new_relic" / "incidents_response.json"
)


def _load_fixture_rows() -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE_PATH.read_text())
    return payload["data"]["actor"]["account"]["nrql"]["results"]  # type: ignore[no-any-return]


def _tool_call_response(tool_calls: list[ToolCall]) -> MagicMock:
    response = MagicMock()
    response.tool_calls = tool_calls
    response.has_tool_calls = True
    response.content = ""
    response.raw_content = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
            }
            for tc in tool_calls
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


def _incident_command_diagnosis(summary: str) -> str:
    return (
        "Triage complete: test scope.\n"
        "Status — confirmed: ok | open: none | next: done | owner: on-call\n"
        "Hypotheses:\n"
        "1. Checkout latency regression — confirm: New Relic incidents; rule out: caller-only misconfig\n"
        "Verification:\n"
        "1. New Relic alerts (H1): incidents returned for checkout-service\n"
        "Follow-up questions:\n"
        "1. Was there a recent deploy of checkout-service?\n"
        "Remediation trade-offs: N/A — single clear fix path\n"
        f"{summary}"
    )


def test_new_relic_alert_source_maps_to_tools() -> None:
    """A new_relic-sourced alert auto-seeds the new_relic tools."""
    entry = alert_source_routing()["new_relic"]
    assert entry.seed_tool_sources == ("new_relic",)
    assert entry.relevance_tool_sources == ("new_relic",)


def test_new_relic_tools_are_registered() -> None:
    """Both New Relic tools are discoverable through the real registry."""
    names = {t.name for t in get_registered_tools() if t.source == "new_relic"}
    assert names == {"query_new_relic_alerts", "query_new_relic_metrics"}


def test_planner_discovers_and_invokes_new_relic_alerts_through_the_real_loop() -> None:
    """The real ReAct loop calls ``query_new_relic_alerts`` and cites its evidence.

    ``get_available_tools`` is intentionally left unpatched so tool discovery
    goes through the real registry filtered by the real ``is_available``
    check; only the LLM and the New Relic client (the vendor/network
    boundary) are mocked.
    """
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_incidents.return_value = {
        "success": True,
        "results": _load_fixture_rows(),
    }

    responses = [
        _tool_call_response([ToolCall(id="c1", name="query_new_relic_alerts", input={})]),
        _text_response(_incident_command_diagnosis("checkout-latency incidents cited.")),
    ]
    mock_llm = MagicMock()
    mock_llm._model = "gpt-4o"
    mock_llm.invoke.side_effect = responses
    mock_llm.build_tool_result_message.side_effect = lambda _calls, results: {
        "role": "user",
        "content": json.dumps(results, default=str),
    }

    state = {
        "alert_name": "Checkout latency incident",
        "pipeline_name": "test-pipeline",
        "severity": "critical",
        # Deliberately no "alert_source" — FR-8/FR-9 auto-seeding is already
        # covered by tests/core/domain/alerts/test_alert_source.py and
        # tests/cli/test_smoke.py::test_investigate_print_template_new_relic_smoke.
        # This test isolates the other half: the LLM-driven planner discovering
        # and invoking the tool on its own through the real registry.
        "resolved_integrations": {
            "new_relic": {
                "api_key": "NRAK-test-fake-0000000000000000000",
                "account_id": "9876543",
                "base_url": "https://api.newrelic.com",
            }
        },
    }

    with (
        patch(
            "tools.investigation.stages.gather_evidence.agent.default_llm_factory",
            return_value=mock_llm,
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.get_tracker", return_value=MagicMock()
        ),
        patch(
            "integrations.new_relic.tools.new_relic_alerts_tool.tool.NewRelicClient",
            return_value=mock_client,
        ),
    ):
        result = ConnectedInvestigationAgent().run(state)

    mock_client.query_incidents.assert_called_once()
    assert any(
        isinstance(m.get("content"), str) and "checkout-latency" in m["content"]
        for m in result["agent_messages"]
    )
