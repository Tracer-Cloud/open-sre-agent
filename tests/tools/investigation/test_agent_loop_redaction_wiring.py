"""The investigation wrapper delegates its tool loop without repeating redaction work."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent import Agent
from core.llm.types import AgentLLMResponse, ToolCall
from tools.investigation.stages.gather_evidence import agent as agent_module
from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent

TOOL_INPUT: dict[str, Any] = {"query": "status:error", "api_key": "sk-should-be-hidden"}


class _LLM:
    """Request one tool call, then return a complete conclusion."""

    model_id = "fake-model"

    def __init__(self) -> None:
        self._invocations = 0

    def tool_schemas(self, _tools: Any) -> list[Any]:
        return []

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.input} for call in tool_calls
            ],
        }

    def build_tool_result_message(
        self, _tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {"role": "tool", "content": str(results)}

    def invoke(self, *_args: Any, **_kwargs: Any) -> AgentLLMResponse:
        self._invocations += 1
        if self._invocations == 1:
            return AgentLLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-1", name="query_logs", input=TOOL_INPUT)],
            )
        return AgentLLMResponse(
            content=(
                "Triage complete: checkout only.\n"
                "Status — confirmed: errors | open: cause | next: mitigate | owner: on-call\n"
                "Hypotheses:\n1. Bad deploy — confirm: logs; rule out: healthy deploy\n"
                "Verification:\n1. Logs (H1): errors found\n"
                "Follow-up questions:\n1. Was checkout deployed?\n"
                "Remediation trade-offs: N/A — single clear fix path\n"
                "Root cause: a bad deploy."
            ),
            tool_calls=[],
        )


class _Tool:
    name = "query_logs"
    source = "test"

    @staticmethod
    def validate_public_input(_value: dict[str, Any]) -> None:
        return None

    @staticmethod
    def extract_params(_resolved: dict[str, Any]) -> dict[str, Any]:
        return {}

    @staticmethod
    def run(**_kwargs: Any) -> dict[str, Any]:
        return {"hits": 1}


class _Tracker:
    def start(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""

    def record_tool_start(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""

    def record_tool_end(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""

    def complete(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""


def test_investigation_uses_built_agent_and_redacts_tool_input_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walked: list[Any] = []
    built: list[Agent[Any]] = []
    real_redact_view = agent_module.redact_tool_view
    real_build_agent = agent_module.build_agent

    def _counting_view(tool_input: Any, output: Any = None) -> Any:
        walked.append(tool_input)
        return real_redact_view(tool_input, output)

    def _record_build(config: Any) -> Agent[Any]:
        agent = real_build_agent(config)
        built.append(agent)
        return agent

    monkeypatch.setattr(agent_module, "redact_tool_view", _counting_view)
    monkeypatch.setattr(agent_module, "build_agent", _record_build)
    monkeypatch.setattr(agent_module, "get_tracker", _Tracker)
    monkeypatch.setattr(agent_module, "get_available_tools", lambda _resolved: [_Tool()])
    monkeypatch.setattr(agent_module, "select_investigation_tools", lambda tools, _state: tools)
    monkeypatch.setattr(agent_module, "build_seed_calls", lambda _state, _tools, _llm: [])
    monkeypatch.setattr(agent_module, "build_investigation_system_prompt", lambda _state: "sys")
    monkeypatch.setattr(agent_module, "format_alert_context", lambda _state, _tools: "alert")

    result = ConnectedInvestigationAgent(llm_factory=_LLM).run(
        {"resolved_integrations": {}, "alert_name": "cpu"}  # type: ignore[arg-type]
    )

    assert len(built) == 1
    assert isinstance(built[0], Agent)
    assert isinstance(result, dict)
    assert isinstance(result["evidence_entries"], list)
    assert len([value for value in walked if value is TOOL_INPUT]) == 1
