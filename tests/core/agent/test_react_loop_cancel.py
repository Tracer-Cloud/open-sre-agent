"""ReAct loop cooperatively stops when the host console requests cancel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent import Agent
from core.llm.types import AgentLLMResponse


@dataclass
class _CancelConsole:
    cancel_requested: bool = True


class _BoomLLM:
    """Must not be called once cancel is already requested."""

    model_id = "test-model"

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        raise AssertionError("ReAct loop must not call the LLM after cancel_requested")

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


def test_react_loop_stops_before_llm_when_cancel_requested() -> None:
    agent = Agent(
        llm=_BoomLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        tool_resources={"action_tool_context": type("Ctx", (), {"console": _CancelConsole()})()},
        max_iterations=3,
    )
    result = agent.run([{"role": "user", "content": "hello"}])
    assert result.terminated_by_tool is True
    assert result.hit_iteration_cap is False
    assert result.llm_iterations_used == 1
    assert result.final_text == ""
