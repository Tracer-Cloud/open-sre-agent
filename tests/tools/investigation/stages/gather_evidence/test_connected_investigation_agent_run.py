"""Behavioral tests for ConnectedInvestigationAgent.run() after the build_agent() refactor.

Constructs the agent with a fake, in-process LLM client (no real network calls,
no get_llm()) and drives a full run() to confirm the loop still produces
correct evidence, duplicate-call suppression, and state-update shapes now that
construction/execution goes through core.agent_harness.agent_builder.build_agent()
instead of a hand-rolled loop.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

from core.llm.types import AgentLLMResponse, ToolCall
from core.state import InvestigationState
from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent


class _FakeInvestigationTool:
    """Minimal stand-in exposing only what execute_tool_calls/build_connected_tool_context touch."""

    def __init__(self, name: str, source: str, output: dict[str, Any]) -> None:
        self.name = name
        self.source = source
        self.public_input_schema: dict[str, Any] = {"properties": {}}
        self._output = output

    def validate_public_input(self, _value: dict[str, Any]) -> str | None:
        return None

    def extract_params(self, _resolved: dict[str, Any]) -> dict[str, Any]:
        return {}

    def run(self, **_kwargs: Any) -> dict[str, Any]:
        return self._output


class _FakeInvestigationLLM:
    """Deterministic, in-process stand-in for the tool-calling LLM.

    Returns one canned response per call to invoke(), in order: real tool
    call, then a repeat of the same call (to exercise duplicate suppression),
    then a text-only conclusion.
    """

    def __init__(self, responses: list[AgentLLMResponse]) -> None:
        self._responses = list(responses)
        self.invoke_count = 0

    @property
    def model_id(self) -> str | None:
        return "fake-model"

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        _ = tools
        response = self._responses[self.invoke_count]
        self.invoke_count += 1
        return response

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.input} for tc in tool_calls
            ],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": [
                {"id": tc.id, "name": tc.name, "result": result}
                for tc, result in zip(tool_calls, results)
            ],
        }


def _make_state() -> InvestigationState:
    return cast(
        InvestigationState,
        {
            "raw_alert": {"alert_id": "a1"},
            "alert_name": "Pipeline Error",
            "severity": "critical",
            "alert_source": "grafana",
            "problem_md": "Something broke.",
            "resolved_integrations": {},
            "planned_actions": [],
        },
    )


def _run_agent(responses: list[AgentLLMResponse]) -> tuple[dict[str, Any], list[tuple[str, dict]]]:
    fake_tool = _FakeInvestigationTool(
        name="query_grafana_logs", source="grafana", output={"logs": ["boom"]}
    )
    events: list[tuple[str, dict[str, Any]]] = []

    def _on_event(kind: str, data: dict[str, Any]) -> None:
        events.append((kind, data))

    with (
        patch(
            "tools.investigation.stages.gather_evidence.agent.get_available_tools",
            return_value=[fake_tool],
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.default_llm_factory",
            return_value=_FakeInvestigationLLM(responses),
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.incident_command_conclusion_complete",
            return_value=True,
        ),
        # Seed calls are pre-existing, unchanged behavior (not what this
        # refactor touches) — disabled here to isolate the loop delegation.
        patch(
            "tools.investigation.stages.gather_evidence.agent.build_seed_calls",
            return_value=[],
        ),
    ):
        agent = ConnectedInvestigationAgent()
        result = agent.run(_make_state(), on_event=_on_event)
    return result, events


def _tool_call_response(call_id: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name="query_grafana_logs", input={})],
        raw_content=None,
    )


class TestConnectedInvestigationAgentRun:
    def test_returns_state_update_dict_with_expected_keys(self) -> None:
        result, _events = _run_agent(
            [
                _tool_call_response("call_1"),
                AgentLLMResponse(content="Final diagnosis text.", tool_calls=[], raw_content=None),
            ]
        )
        assert isinstance(result, dict)
        for key in (
            "evidence",
            "evidence_entries",
            "agent_messages",
            "executed_hypotheses",
            "investigation_loop_count",
            "investigation_iteration_cap",
            "connected_integrations",
            "available_action_names",
        ):
            assert key in result

    def test_merges_evidence_from_the_executed_tool_call(self) -> None:
        result, _events = _run_agent(
            [
                _tool_call_response("call_1"),
                AgentLLMResponse(content="Final diagnosis text.", tool_calls=[], raw_content=None),
            ]
        )
        assert result["evidence"]["query_grafana_logs"] == {"logs": ["boom"]}
        assert len(result["evidence_entries"]) == 1
        assert result["evidence_entries"][0]["tool_name"] == "query_grafana_logs"
        assert result["evidence_entries"][0]["loop_iteration"] == 0

    def test_agent_messages_are_plain_provider_dicts(self) -> None:
        result, _events = _run_agent(
            [
                _tool_call_response("call_1"),
                AgentLLMResponse(content="Final diagnosis text.", tool_calls=[], raw_content=None),
            ]
        )
        agent_messages = result["agent_messages"]
        assert isinstance(agent_messages, list)
        assert agent_messages
        assert all(isinstance(m, dict) for m in agent_messages)

    def test_duplicate_tool_call_is_suppressed_not_reexecuted(self) -> None:
        result, _events = _run_agent(
            [
                _tool_call_response("call_1"),
                _tool_call_response("call_2"),  # same tool/args again — should be suppressed
                AgentLLMResponse(content="Final diagnosis text.", tool_calls=[], raw_content=None),
            ]
        )
        # Only one evidence entry: the duplicate call must not merge evidence twice.
        assert len(result["evidence_entries"]) == 1
        assert result["investigation_loop_count"] == 3

    def test_emits_agent_start_and_agent_end_events(self) -> None:
        _result, events = _run_agent(
            [
                _tool_call_response("call_1"),
                AgentLLMResponse(content="Final diagnosis text.", tool_calls=[], raw_content=None),
            ]
        )
        kinds = [kind for kind, _data in events]
        assert kinds[0] == "agent_start"
        assert kinds[-1] == "agent_end"
        assert "tool_start" in kinds
        assert "tool_end" in kinds


class _RaisingLLM(_FakeInvestigationLLM):
    """Raises on the first invoke() instead of returning a response."""

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,  # noqa: ARG002 - satisfies AgentLLMClient.invoke's shape
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        raise TimeoutError("simulated LLM timeout")


class TestConnectedInvestigationAgentLlmFailure:
    """A classified LLM-invoke failure must degrade gracefully, not crash the pipeline."""

    def test_llm_timeout_returns_degraded_state_instead_of_raising(self) -> None:
        fake_tool = _FakeInvestigationTool(
            name="query_grafana_logs", source="grafana", output={"logs": ["boom"]}
        )
        with (
            patch(
                "tools.investigation.stages.gather_evidence.agent.get_available_tools",
                return_value=[fake_tool],
            ),
            patch(
                "tools.investigation.stages.gather_evidence.agent.default_llm_factory",
                return_value=_RaisingLLM([]),
            ),
            patch(
                "tools.investigation.stages.gather_evidence.agent.build_seed_calls",
                return_value=[],
            ),
        ):
            agent = ConnectedInvestigationAgent()
            result = agent.run(_make_state())

        assert result["validity_score"] == 0.0
        assert "timeout" in result["root_cause"].lower()
        assert result["evidence"] == {}
        assert result["evidence_entries"] == []
