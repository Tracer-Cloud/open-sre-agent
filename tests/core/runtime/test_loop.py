from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest

from core.runtime.agent import Agent, AgentRunResult
from core.runtime.events import (
    MessageUpdateEvent,
    RuntimeEvent,
    ToolExecutionUpdateEvent,
)
from core.runtime.llm.agent_llm_client import AgentLLMResponse, ToolCall
from core.runtime.messages import (
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
    app_runtime_message,
    user_runtime_message,
)
from core.runtime.types import AgentTool, AgentToolContext
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
        self.seen_messages: list[list[dict[str, Any]]] = []

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
        self.seen_messages.append(messages)
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
    llm: FakeLLM,
    tools: list[Any],
    max_iterations: int = 5,
    on_event: Any = None,
    on_runtime_event: Any = None,
) -> Agent:
    return Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=max_iterations,
        on_event=on_event,
        on_runtime_event=on_runtime_event,
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
    assert isinstance(result.messages[0], UserRuntimeMessage)
    assert result.messages[0].content == initial[0]["content"]
    assert isinstance(result.messages[2], ToolResultRuntimeMessage)
    assert llm.seen_messages[0] == initial


def test_agent_transcript_can_keep_app_messages_out_of_provider_context() -> None:
    llm = FakeLLM(iter([_text_response("done")]))

    result = _agent(llm, _tools(FakeTool("query_logs"))).run(
        [
            user_runtime_message("hello"),
            app_runtime_message("ui-note", "render only", include_in_context=False),
            app_runtime_message("runtime-context", "visible context"),
        ]
    )

    assert result.final_text == "done"
    assert [message["content"] for message in llm.seen_messages[0]] == [
        "hello",
        "visible context",
    ]
    assert len(result.messages) == 4


def test_runtime_events_emit_typed_lifecycle_and_streaming_order() -> None:
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "query_logs"),
                _text_response("final"),
            ]
        )
    )
    events: list[RuntimeEvent] = []

    _agent(llm, _tools(FakeTool("query_logs")), on_runtime_event=events.append).run(
        [{"role": "user", "content": "hello"}]
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "provider_request_start",
        "provider_request_end",
        "message_start",
        "tool_execution_start",
        "tool_execution_end",
        "turn_end",
        "turn_start",
        "provider_request_start",
        "provider_request_end",
        "message_start",
        "message_update",
        "turn_end",
        "agent_end",
    ]
    message_updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [event.delta for event in message_updates] == ["final"]


def test_legacy_on_event_bridge_emits_kinds_in_order() -> None:
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

    assert events == [
        "agent_start",
        "llm_start",
        "tool_start",
        "tool_end",
        "llm_start",
        "agent_end",
    ]


def test_on_event_failure_is_logged_and_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLM(iter([_text_response("final")]))

    def on_event(_kind: str, _data: dict[str, Any]) -> None:
        raise RuntimeError("broken renderer")

    with caplog.at_level(logging.DEBUG, logger="core.runtime.agent"):
        result = _agent(llm, _tools(FakeTool("query_logs")), on_event=on_event).run(
            [{"role": "user", "content": "hello"}]
        )

    assert result.final_text == "final"
    assert "[runtime] on_event(agent_start) raised; ignoring" in caplog.text


def test_steer_injects_message_before_next_llm_turn() -> None:
    llm = FakeLLM(iter([_text_response("final")]))
    agent = _agent(llm, _tools(FakeTool("query_logs")))

    agent.steer("look at the newest deploy first")
    result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "final"
    assert [message["content"] for message in llm.seen_messages[0]] == [
        "hello",
        "look at the newest deploy first",
    ]


def test_follow_up_runs_after_an_accepted_final_answer() -> None:
    llm = FakeLLM(iter([_text_response("first answer"), _text_response("follow-up answer")]))
    agent = _agent(llm, _tools(FakeTool("query_logs")), max_iterations=3)

    agent.follow_up("now summarize the remediation")
    result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "follow-up answer"
    assert llm.invocations == 2
    assert [message["content"] for message in llm.seen_messages[1]] == [
        "hello",
        "first answer",
        "now summarize the remediation",
    ]


def test_agent_tool_context_update_emits_tool_execution_update() -> None:
    def execute(_payload: dict[str, Any], context: AgentToolContext) -> dict[str, Any]:
        assert context.on_update is not None
        context.on_update({"status": "halfway"})
        return {"ok": True}

    tool = AgentTool(
        name="agent_tool",
        description="test tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execute=execute,
    )
    llm = FakeLLM(iter([_tool_call_response("c1", "agent_tool"), _text_response("done")]))
    events: list[RuntimeEvent] = []

    result = _agent(llm, [tool], on_runtime_event=events.append).run(
        [{"role": "user", "content": "hello"}]
    )

    assert result.final_text == "done"
    updates = [event for event in events if isinstance(event, ToolExecutionUpdateEvent)]
    assert len(updates) == 1
    assert updates[0].tool_call_id == "c1"
    assert updates[0].tool_name == "agent_tool"
    assert updates[0].partial_result == {"status": "halfway"}


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
