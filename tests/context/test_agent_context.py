from __future__ import annotations

import pytest

from context.agent_context import AgentContext
from context.models import coerce_messages
from core.agent_runtime import Agent
from core.llm.agent_llm_client import AgentLLMResponse
from core.messages import UserRuntimeMessage
from core.types import AgentTool


class _NoToolLLM:
    def tool_schemas(self, _tools: list[AgentTool]) -> list[dict[str, object]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, object]],
        *,
        system: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ) -> AgentLLMResponse:
        assert system == "runtime system"
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


def test_agent_context_can_be_runtime_request_without_shell_metadata() -> None:
    tool = _tool()
    ctx = AgentContext(
        text="investigate",
        conversation_messages=coerce_messages([]),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        system_prompt="runtime system",
        available_tools=(tool,),
        active_tools=(tool,),
        resolved_integrations={},
        max_iterations=2,
    )

    result = Agent(
        llm=_NoToolLLM(),
        system="ignored legacy system",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    ).run(agent_context=ctx)

    assert result.final_text == "done"
    assert result.hit_iteration_cap is False
    assert isinstance(result.messages[0], UserRuntimeMessage)
    assert result.messages[0].content == "investigate"


def test_agent_context_runtime_validation_requires_runtime_fields() -> None:
    ctx = AgentContext(
        text="investigate",
        conversation_messages=coerce_messages([]),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )

    with pytest.raises(ValueError, match="system_prompt"):
        ctx.validate_runtime_request()
