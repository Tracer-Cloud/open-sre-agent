"""Textual tool-call salvage: a tool invocation emitted as plain text must not
end the turn as a "conclusion".

Regression for the interactive-shell chain break where "remove the existing
cron loops" ran ``/cron list`` and then silently stopped: the model emitted the
follow-up call as reply text (``{"command": "/cron", "args": ["remove", ...]}``)
instead of a structured tool call, and the loop accepted that JSON blob as the
final answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from core.agent.agent import Agent
from core.agent.textual_tool_call import TEXTUAL_TOOL_CALL_NUDGE, looks_like_textual_tool_call
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool_framework.registered_tool import RegisteredTool


class _FakeLLM:
    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.model_id: str | None = None
        self.seen_messages: list[list[dict[str, Any]]] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": t.name} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        self.seen_messages.append(list(messages))
        return next(self._responses)

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [{"id": tc.id, "output": output} for tc, output in zip(tool_calls, results)],
        }


class _SlashLikeTool:
    """Minimal tool with the slash_invoke argument shape."""

    name = "slash_invoke"
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}, "args": {"type": "array"}},
        "required": ["command"],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def validate_public_input(self, value: dict[str, Any]) -> str | None:  # noqa: ARG002
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ok": True}


_ARGS_JSON = '{"command": "/cron", "args": ["remove", "ecf7c2580b83"]}'


def _text(content: str) -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)


def _tool_call() -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[
            ToolCall(
                id="tc0",
                name="slash_invoke",
                input={"command": "/cron", "args": ["remove", "ecf7c2580b83"]},
            )
        ],
        raw_content=None,
    )


class TestDetector:
    def test_matches_bare_argument_object(self) -> None:
        assert looks_like_textual_tool_call(_ARGS_JSON, [_SlashLikeTool()])

    def test_matches_fenced_argument_object(self) -> None:
        fenced = f"```json\n{_ARGS_JSON}\n```"
        assert looks_like_textual_tool_call(fenced, [_SlashLikeTool()])

    def test_matches_named_call_envelope(self) -> None:
        envelope = '{"name": "slash_invoke", "arguments": {"command": "/cron"}}'
        assert looks_like_textual_tool_call(envelope, [_SlashLikeTool()])

    def test_prose_containing_json_does_not_match(self) -> None:
        prose = f"I would run {_ARGS_JSON} next, but the task is done."
        assert not looks_like_textual_tool_call(prose, [_SlashLikeTool()])

    def test_unknown_keys_do_not_match(self) -> None:
        assert not looks_like_textual_tool_call(
            '{"command": "/cron", "unexpected": 1}', [_SlashLikeTool()]
        )

    def test_missing_required_key_does_not_match(self) -> None:
        assert not looks_like_textual_tool_call('{"args": ["list"]}', [_SlashLikeTool()])

    def test_empty_object_does_not_match(self) -> None:
        assert not looks_like_textual_tool_call("{}", [_SlashLikeTool()])

    def test_plain_conclusion_does_not_match(self) -> None:
        assert not looks_like_textual_tool_call("All four tasks removed.", [_SlashLikeTool()])

    def test_matches_array_of_argument_objects(self) -> None:
        batch = (
            '[{"command": "/cron", "args": ["remove", "a1"]},'
            ' {"command": "/cron", "args": ["remove", "b2"]}]'
        )
        assert looks_like_textual_tool_call(batch, [_SlashLikeTool()])

    def test_matches_newline_separated_argument_objects(self) -> None:
        batch = (
            '{"command": "/cron", "args": ["remove", "a1"]}\n'
            '{"command": "/cron", "args": ["remove", "b2"]}'
        )
        assert looks_like_textual_tool_call(batch, [_SlashLikeTool()])

    def test_array_with_one_prose_element_does_not_match(self) -> None:
        assert not looks_like_textual_tool_call(
            '[{"command": "/cron"}, {"note": "done"}]', [_SlashLikeTool()]
        )

    def test_question_closing_does_not_match(self) -> None:
        assert not looks_like_textual_tool_call(
            "Found 5 loops — remove all of them?", [_SlashLikeTool()]
        )


class TestLoopNudge:
    def test_textual_tool_call_is_nudged_then_executed(self) -> None:
        tool = _SlashLikeTool()
        llm = _FakeLLM(iter([_text(_ARGS_JSON), _tool_call(), _text("removed ecf7c2580b83")]))
        agent: Agent[RegisteredTool] = Agent(
            llm=llm,
            system="sys",
            tools=cast("list[RegisteredTool]", [tool]),
            resolved_integrations={},
            max_iterations=5,
        )

        result = agent.run([{"role": "user", "content": "remove the existing cron loops"}])

        assert tool.calls, "the salvaged tool call must actually execute"
        assert result.final_text == "removed ecf7c2580b83"
        # The nudge rode into the model's next request as a user message.
        followup_request = llm.seen_messages[1]
        assert any(
            TEXTUAL_TOOL_CALL_NUDGE in str(message.get("content", ""))
            for message in followup_request
        )

    def test_nudges_are_capped_then_reply_is_accepted(self) -> None:
        llm = _FakeLLM(iter([_text(_ARGS_JSON)] * 5))
        agent: Agent[RegisteredTool] = Agent(
            llm=llm,
            system="sys",
            tools=cast("list[RegisteredTool]", [_SlashLikeTool()]),
            resolved_integrations={},
            max_iterations=5,
        )

        result = agent.run([{"role": "user", "content": "remove the existing cron loops"}])

        # Two nudges, then the third identical reply is accepted as the answer:
        # 3 LLM invocations, not the full 5-iteration budget.
        assert len(llm.seen_messages) == 3
        assert result.final_text == _ARGS_JSON
        assert result.hit_iteration_cap is False
