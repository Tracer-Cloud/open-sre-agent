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
    assert result.cancelled is True
    assert result.terminated_by_tool is False
    assert result.hit_iteration_cap is False
    assert result.llm_iterations_used == 1
    assert result.final_text == ""


def test_react_loop_stops_after_iteration_when_cancel_flips() -> None:
    """Cancel between iterations must not start another LLM call."""
    import threading

    from core.llm.types import ToolCall

    cancel = threading.Event()

    class _FlipConsole:
        @property
        def cancel_requested(self) -> bool:
            return cancel.is_set()

    class _NoopTool:
        name = "noop"
        description = "noop"
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

        def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    class _CountingLLM(_BoomLLM):
        def __init__(self) -> None:
            self.calls = 0

        def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
            return [
                {
                    "name": getattr(t, "name", "noop"),
                    "description": "",
                    "parameters": getattr(t, "parameters", {}),
                }
                for t in tools
            ]

        def invoke(
            self,
            _messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            _ = (system, tools)
            self.calls += 1
            if self.calls == 1:
                # After tools run, cancel is set so iteration 2 must not call us.
                cancel.set()
                return AgentLLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="noop", input={})],
                    raw_content=None,
                )
            raise AssertionError("second LLM iteration must not run after cancel")

    llm = _CountingLLM()
    agent = Agent(
        llm=llm,
        system="sys",
        tools=[_NoopTool()],
        resolved_integrations={},
        tool_resources={"action_tool_context": type("Ctx", (), {"console": _FlipConsole()})()},
        max_iterations=5,
    )
    result = agent.run([{"role": "user", "content": "hello"}])
    assert llm.calls == 1
    assert result.cancelled is True
    assert result.terminated_by_tool is False
