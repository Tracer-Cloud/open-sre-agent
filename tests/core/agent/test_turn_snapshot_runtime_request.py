from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.agent import Agent
from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.prompts import PromptEnvelope
from core.agent_harness.prompts.conversation_memory import MAX_CONVERSATION_MESSAGES
from core.llm.types import AgentLLMResponse
from core.messages import UserRuntimeMessage
from core.types import AgentTool


class _NoToolLLM:
    def __init__(self) -> None:
        self.seen_system: str | None = None
        self.seen_messages: list[dict[str, object]] | None = None

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        self.seen_system = system
        self.seen_messages = messages
        assert tools == []
        return AgentLLMResponse(content="done", tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


def _tool() -> AgentTool:
    return AgentTool(
        name="inspect",
        description="inspect",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execute=lambda _payload, _ctx: {"ok": True},
    )


def _turn_snapshot(**overrides: Any) -> TurnSnapshot:
    values: dict[str, Any] = {
        "text": "investigate",
        "conversation_messages": (),
        "configured_integrations": (),
        "configured_integrations_known": True,
        "last_state": None,
        "last_synthetic_observation_path": None,
        "reasoning_effort": None,
    }
    values.update(overrides)
    return TurnSnapshot(**values)


def test_turn_snapshot_can_drive_agent_runtime_request() -> None:
    tool = _tool()
    llm = _NoToolLLM()
    ctx = _turn_snapshot(
        system_prompt=PromptEnvelope.from_text("runtime system"),
        available_tools=(tool,),
        active_tools=(tool,),
        resolved_integrations={"github": {"configured": True}},
        max_iterations=2,
    )

    result = Agent(
        llm=llm,
        system="ignored legacy system",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    ).run(runtime_request=ctx)

    assert result.final_text == "done"
    assert result.hit_iteration_cap is False
    assert isinstance(result.messages[0], UserRuntimeMessage)
    assert result.messages[0].content == "investigate"
    assert llm.seen_system == "runtime system"
    assert llm.seen_messages == [{"role": "user", "content": "investigate"}]


def test_agent_context_falls_back_to_process_wide_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``llm=`` is omitted at construction, ``run(runtime_request=...)`` resolves
    the process-wide client via :func:`agent_llm_client.get_agent_llm`."""
    tool = _tool()
    built = _NoToolLLM()
    monkeypatch.setattr("core.llm.agent_llm_client.get_agent_llm", lambda: built)

    ctx = _turn_snapshot(
        system_prompt=PromptEnvelope.from_text("runtime system"),
        available_tools=(tool,),
        active_tools=(tool,),
        resolved_integrations={"github": {"configured": True}},
        max_iterations=2,
    )

    result = Agent[Any](max_iterations=1).run(runtime_request=ctx)

    assert result.final_text == "done"
    assert built.seen_system == "runtime system"


def test_turn_snapshot_runtime_validation_requires_runtime_fields() -> None:
    with pytest.raises(ValueError, match="system_prompt"):
        _turn_snapshot().validate_runtime_request()

    with pytest.raises(ValueError, match="max_iterations"):
        _turn_snapshot(system_prompt="runtime system", max_iterations=0).validate_runtime_request()

    with pytest.raises(ValueError, match="active_tools"):
        _turn_snapshot(system_prompt="runtime system", max_iterations=1).validate_runtime_request()


@dataclass(frozen=True)
class _RuntimeInput:
    text: str
    messages: tuple[tuple[str, str], ...]
    system_prompt: str
    available_tools: tuple[AgentTool, ...]
    active_tools: tuple[AgentTool, ...]
    resolved_integrations: dict[str, Any]
    max_iterations: int
    model: object
    last_observation: str | None


class _AgentState:
    def __init__(self, tool: AgentTool) -> None:
        self._tool = tool
        self.seen_text: str | None = None
        self.model = object()

    def select_agent_context_input(self, text: str) -> _RuntimeInput:
        self.seen_text = text
        return _RuntimeInput(
            text=text,
            messages=(),
            system_prompt="selected system",
            available_tools=(self._tool,),
            active_tools=(self._tool,),
            resolved_integrations={"sentry": {"configured": True}},
            max_iterations=3,
            model=self.model,
            last_observation="prior observation",
        )


class _Session:
    def __init__(self, tool: AgentTool) -> None:
        self.cli_agent_messages = [
            ("user", str(index)) for index in range(MAX_CONVERSATION_MESSAGES + 2)
        ]
        self.configured_integrations = ("github",)
        self.configured_integrations_known = True
        self.last_state = {"root_cause": "db saturation"}
        self.last_synthetic_observation_path = "/tmp/observation.json"
        self.reasoning_effort = None
        self.agent = _AgentState(tool)


def test_turn_snapshot_from_session_reads_last_command_observation_from_session() -> None:
    class _Session:
        cli_agent_messages: list[tuple[str, str]] = []
        configured_integrations = ()
        configured_integrations_known = True
        last_state = None
        last_synthetic_observation_path = None
        reasoning_effort = None
        last_command_observation = "tool output from shell"

    ctx = TurnSnapshot.from_session("why", _Session())

    assert ctx.last_observation == "tool output from shell"


def test_turn_snapshot_from_session_snapshots_shell_and_runtime_request_fields() -> None:
    tool = _tool()
    session = _Session(tool)

    ctx = TurnSnapshot.from_session("next turn", session)

    assert session.agent.seen_text == "next turn"
    assert ctx.text == "next turn"
    assert len(ctx.conversation_messages) == MAX_CONVERSATION_MESSAGES
    assert ctx.conversation_messages[0] == ("user", "2")
    assert ctx.configured_integrations == ("github",)
    assert ctx.last_state == {"root_cause": "db saturation"}
    assert ctx.last_synthetic_observation_path == "/tmp/observation.json"
    assert ctx.render_system_prompt() == "selected system"
    assert ctx.available_tools == (tool,)
    assert ctx.active_tools == (tool,)
    assert ctx.resolved_integrations == {"sentry": {"configured": True}}
    assert ctx.max_iterations == 3
    assert ctx.model is session.agent.model
    assert ctx.last_observation == "prior observation"
