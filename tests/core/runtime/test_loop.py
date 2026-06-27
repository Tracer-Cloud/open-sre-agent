from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest

from core.runtime.agent import Agent, AgentRunResult
from core.runtime.llm.agent_llm_client import AgentLLMResponse, ToolCall
from tools.registered_tool import RegisteredTool


class FakeLLM:
    """Duck-typed agent LLM client driving a scripted response sequence.

    Deliberately NOT a subclass of any real provider client so that the
    isinstance branches in ``build_assistant_message`` / ``build_tool_result_messages``
    fall through to the generic path.
    """

    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.invocations = 0
        self.schema_tool_names: list[list[str]] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        self.schema_tool_names.append([t.name for t in tools])
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


def _agent(
    llm: FakeLLM, tools: list[RegisteredTool], max_iterations: int = 5, on_event: Any = None
) -> Agent:
    return Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=max_iterations,
        on_event=on_event,
    )


def test_immediate_final_answer_executes_no_tools() -> None:
    llm = FakeLLM(iter([_text_response("done immediately")]))

    result = _agent(llm, _tools(FakeTool("query_logs"))).run([{"role": "user", "content": "hello"}])

    assert isinstance(result, AgentRunResult)
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
    initial: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]

    result = _agent(llm, _tools(FakeTool("query_logs", output))).run(initial)

    assert len(result.executed) == 1
    tc, tool_output = result.executed[0]
    assert isinstance(tc, ToolCall)
    assert tc.name == "query_logs"
    assert tool_output == output
    assert result.final_text == "here is the answer"
    assert result.hit_iteration_cap is False
    # user + assistant(tool call) + tool-result + assistant(final)
    assert len(result.messages) == 4
    assert result.messages[0] == initial[0]


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

    _agent(llm, _tools(FakeTool("query_logs")), on_event=on_event).run(
        [{"role": "user", "content": "hello"}]
    )

    assert events == ["llm_start", "tool_start", "tool_end", "llm_start"]


def test_on_event_failure_is_logged_and_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLM(iter([_text_response("final")]))

    def on_event(_kind: str, _data: dict[str, Any]) -> None:
        raise RuntimeError("broken renderer")

    with caplog.at_level(logging.DEBUG, logger="core.runtime.agent"):
        result = _agent(llm, _tools(FakeTool("query_logs")), on_event=on_event).run(
            [{"role": "user", "content": "hello"}]
        )

    assert result.final_text == "final"
    assert "[runtime] on_event(llm_start) raised; ignoring" in caplog.text


def test_rejecting_conclusion_without_nudge_raises() -> None:
    class RejectingAgent(Agent[RegisteredTool]):
        def _should_accept_conclusion(
            self,
            *,
            evidence_count: int,  # noqa: ARG002
            iteration: int,  # noqa: ARG002
        ) -> tuple[bool, str | None]:
            return False, None

    llm = FakeLLM(iter([_text_response("not enough")]))
    agent = RejectingAgent(
        llm=llm,
        system="sys",
        tools=_tools(FakeTool("query_logs")),
        resolved_integrations={},
        max_iterations=3,
    )

    with pytest.raises(ValueError, match="_should_accept_conclusion returned"):
        agent.run([{"role": "user", "content": "hello"}])


def test_tool_filtering_runs_after_subclass_initialization() -> None:
    class LateStateFilteringAgent(Agent[RegisteredTool]):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.allowed_tool_names = {"keep"}

        def _filter_tools(self, tools: list[RegisteredTool]) -> list[RegisteredTool]:
            return [tool for tool in tools if tool.name in self.allowed_tool_names]

    output = {"value": 42}
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "keep"),
                _text_response("done"),
            ]
        )
    )
    agent = LateStateFilteringAgent(
        llm=llm,
        system="sys",
        tools=_tools(FakeTool("drop"), FakeTool("keep", output)),
        resolved_integrations={},
        max_iterations=3,
    )

    result = agent.run([{"role": "user", "content": "hello"}])

    assert llm.schema_tool_names == [["keep"]]
    assert [(tc.name, tool_output) for tc, tool_output in result.executed] == [("keep", output)]


def test_always_tool_call_hits_iteration_cap() -> None:
    def always_tool_calls() -> Iterator[AgentLLMResponse]:
        counter = 0
        while True:
            counter += 1
            yield _tool_call_response(f"c{counter}", "query_logs")

    max_iterations = 3
    llm = FakeLLM(always_tool_calls())

    result = _agent(llm, _tools(FakeTool("query_logs")), max_iterations=max_iterations).run(
        [{"role": "user", "content": "hello"}]
    )

    assert result.hit_iteration_cap is True
    assert len(result.executed) == max_iterations
    assert result.final_text == ""
    assert llm.invocations == max_iterations
