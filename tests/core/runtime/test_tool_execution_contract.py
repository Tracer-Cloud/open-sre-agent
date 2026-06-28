from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from core.runtime.agent import Agent
from core.runtime.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
    execute_tools,
)
from core.runtime.llm.agent_llm_client import AgentLLMResponse, ToolCall
from core.runtime.provider import ProviderHooks, ProviderRequest
from core.runtime.types import AgentTool, AgentToolContext


def _schema(required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": required or [],
        "additionalProperties": False,
    }


def _tool(
    name: str = "echo",
    *,
    execute: Any | None = None,
    execution_mode: str | None = None,
) -> AgentTool:
    return AgentTool(
        name=name,
        description="test tool",
        input_schema=_schema(["value"]),
        execute=execute or (lambda args, _ctx: {"value": args["value"]}),
        execution_mode=execution_mode,  # type: ignore[arg-type]
    )


def _call(name: str = "echo", value: str = "ok") -> ToolCall:
    return ToolCall(id=f"{name}-1", name=name, input={"value": value})


def test_execute_tool_calls_validates_arguments_before_execution() -> None:
    called = False

    def execute(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"ok": True}

    result = execute_tool_calls(
        [ToolCall(id="c1", name="echo", input={})],
        [_tool(execute=execute)],
        {},
    )[0]

    assert result.is_error is True
    assert "missing required args" in str(result.content)
    assert called is False
    assert execute_tools([ToolCall(id="c1", name="echo", input={})], [_tool()], {}) == [
        {"error": result.content}
    ]


def test_before_hook_can_block_with_structured_result() -> None:
    def before(_request: ToolExecutionRequest) -> BeforeToolCallResult:
        return BeforeToolCallResult(blocked=True, reason="blocked", details={"policy": "deny"})

    result = execute_tool_calls(
        [_call()],
        [_tool()],
        {},
        hooks=ToolExecutionHooks(before_tool_call=before),
    )[0]

    assert result.is_error is True
    assert result.content == "blocked"
    assert result.details == {"policy": "deny"}


def test_after_hook_can_patch_result_and_terminate() -> None:
    def after(
        _request: ToolExecutionRequest,
        _result: ToolExecutionResult,
    ) -> ToolExecutionPatch:
        return ToolExecutionPatch(content="patched", details={"patched": True}, terminate=True)

    result = execute_tool_calls(
        [_call()],
        [_tool()],
        {},
        hooks=ToolExecutionHooks(after_tool_call=after),
    )[0]

    assert result.content == "patched"
    assert result.details == {"patched": True}
    assert result.terminate is True


def test_partial_tool_update_events_are_forwarded() -> None:
    updates: list[tuple[str, Any]] = []

    def execute(args: dict[str, Any], ctx: AgentToolContext) -> dict[str, Any]:
        ctx.emit_update({"seen": args["value"]})
        return {"done": True}

    def on_update(request: ToolExecutionRequest, update: Any) -> None:
        updates.append((request.tool_call.name, update))

    execute_tool_calls(
        [_call(value="abc")],
        [_tool(execute=execute)],
        {},
        hooks=ToolExecutionHooks(on_tool_update=on_update),
    )

    assert updates == [("echo", {"seen": "abc"})]


def test_parallel_batch_preserves_provider_order() -> None:
    tools = [
        _tool("first", execute=lambda _args, _ctx: {"order": 1}),
        _tool("second", execute=lambda _args, _ctx: {"order": 2}),
    ]
    calls = [_call("second", "x"), _call("first", "x")]

    results = execute_tool_calls(calls, tools, {})

    assert [result.details for result in results] == [{"order": 2}, {"order": 1}]


class _FakeLLM:
    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.seen_messages: list[list[dict[str, Any]]] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        _ = tools
        self.seen_messages.append(messages)
        return next(self._responses)

    def build_assistant_message(
        self,
        content: str,
        tool_calls: list[ToolCall],
    ) -> dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": [tc.id for tc in tool_calls]}

    def build_tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {"role": "tool", "results": list(zip([tc.id for tc in tool_calls], results))}


def test_tool_terminate_hint_stops_agent_loop() -> None:
    llm = _FakeLLM(iter([AgentLLMResponse(content="", tool_calls=[_call()], raw_content=None)]))
    tool = _tool(execute=lambda _args, _ctx: ToolExecutionResult(content="done", terminate=True))

    result = Agent(
        llm=llm,
        system="sys",
        tools=[tool],
        resolved_integrations={},
        max_iterations=3,
    ).run([{"role": "user", "content": "go"}])

    assert result.terminated_by_tool is True
    assert result.hit_iteration_cap is False
    assert len(result.tool_results) == 1


def test_provider_boundary_hooks_transform_convert_and_observe() -> None:
    requests: list[ProviderRequest] = []
    llm = _FakeLLM(iter([AgentLLMResponse(content="final", tool_calls=[], raw_content=None)]))

    hooks = ProviderHooks(
        transform_context=lambda messages: list(messages)[-1:],
        convert_to_llm=lambda _llm, messages: [
            {"role": "user", "content": f"converted:{messages[0].content}"}
        ],
        before_provider_request=lambda request: requests.append(request) or request,
        after_provider_response=lambda _request, response: response,
        get_api_key=lambda env_name: f"fake:{env_name}",
    )

    result = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
        provider_hooks=hooks,
    ).run([{"role": "user", "content": "first"}, {"role": "user", "content": "second"}])

    assert result.final_text == "final"
    assert llm.seen_messages == [[{"role": "user", "content": "converted:second"}]]
    assert requests[0].messages == [{"role": "user", "content": "converted:second"}]
    assert hooks.get_api_key is not None
    assert hooks.get_api_key("OPENAI_API_KEY") == "fake:OPENAI_API_KEY"
