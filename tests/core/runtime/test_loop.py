from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from app.core.runtime import ToolLoopResult, run_tool_calling_loop
from app.services.agent_llm_client import AgentLLMResponse, ToolCall
from app.tools.registered_tool import RegisteredTool


class FakeLLM:
    """Duck-typed agent LLM client driving a scripted response sequence.

    Deliberately NOT a subclass of any real provider client so that the
    isinstance branches in ``build_assistant_message`` / ``build_tool_result_messages``
    fall through to the generic ``build_assistant_message`` /
    ``build_tool_result_message`` path.
    """

    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.invocations = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": t.name} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        self.invocations += 1
        return next(self._responses)

    def build_assistant_message(
        self,
        content: str,
        tool_calls: list[ToolCall],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls],
        }

    def build_tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [{"id": tc.id, "output": output} for tc, output in zip(tool_calls, results)],
        }


class FakeTool:
    """Minimal stand-in exposing only what ``execute_tools`` touches."""

    def __init__(self, name: str, output: dict[str, Any] | None = None) -> None:
        self.name = name
        self._output = output if output is not None else {"ok": True}

    def validate_public_input(self, value: dict[str, Any]) -> str | None:  # noqa: ARG002
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return self._output


def _tools(*tools: FakeTool) -> list[RegisteredTool]:
    return cast("list[RegisteredTool]", list(tools))


def _text_response(content: str) -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)


def _tool_call_response(call_id: str, name: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, input={})],
        raw_content=None,
    )


def test_immediate_final_answer_executes_no_tools() -> None:
    llm = FakeLLM(iter([_text_response("done immediately")]))

    result = run_tool_calling_loop(
        llm=llm,
        system="sys",
        messages=[{"role": "user", "content": "hello"}],
        tools=_tools(FakeTool("query_logs")),
        resolved_integrations={},
        max_iterations=5,
    )

    assert isinstance(result, ToolLoopResult)
    assert result.executed == []
    assert result.final_text == "done immediately"
    assert result.hit_iteration_cap is False


def test_one_tool_round_then_final() -> None:
    output = {"value": 42}
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "query_logs"),
                _text_response("here is the answer"),
            ]
        )
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]

    result = run_tool_calling_loop(
        llm=llm,
        system="sys",
        messages=messages,
        tools=_tools(FakeTool("query_logs", output)),
        resolved_integrations={},
        max_iterations=5,
    )

    assert len(result.executed) == 1
    tc, tool_output = result.executed[0]
    assert isinstance(tc, ToolCall)
    assert tc.name == "query_logs"
    assert tool_output == output
    assert result.final_text == "here is the answer"
    assert result.hit_iteration_cap is False
    # user + assistant(tool call) + tool-result + assistant(final)
    assert len(result.messages) == 4
    assert result.messages is messages


def test_on_event_emits_kinds_in_order() -> None:
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "query_logs"),
                _text_response("final"),
            ]
        )
    )
    events: list[str] = []

    def on_event(kind: str, _data: dict[str, Any]) -> None:
        events.append(kind)

    run_tool_calling_loop(
        llm=llm,
        system="sys",
        messages=[{"role": "user", "content": "hello"}],
        tools=_tools(FakeTool("query_logs")),
        resolved_integrations={},
        max_iterations=5,
        on_event=on_event,
    )

    assert events == ["llm_start", "tool_start", "tool_end", "llm_start"]


def test_always_tool_call_hits_iteration_cap() -> None:
    def always_tool_calls() -> Iterator[AgentLLMResponse]:
        counter = 0
        while True:
            counter += 1
            yield _tool_call_response(f"c{counter}", "query_logs")

    max_iterations = 3
    llm = FakeLLM(always_tool_calls())

    result = run_tool_calling_loop(
        llm=llm,
        system="sys",
        messages=[{"role": "user", "content": "hello"}],
        tools=_tools(FakeTool("query_logs")),
        resolved_integrations={},
        max_iterations=max_iterations,
    )

    assert result.hit_iteration_cap is True
    assert len(result.executed) == max_iterations
    assert result.final_text == ""
    assert llm.invocations == max_iterations
