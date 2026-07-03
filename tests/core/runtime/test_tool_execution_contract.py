from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

import core.execution
from core.agent import Agent
from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    _requires_sequential_execution,
    execute_tool_calls,
    execute_tools,
)
from core.llm.types import AgentLLMResponse, ToolCall
from core.provider import ProviderHooks, ProviderRequest
from core.tool_framework.registered_tool import RegisteredTool
from core.types import AgentTool, AgentToolContext


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
    parallel_safe: bool = True,
) -> AgentTool:
    return AgentTool(
        name=name,
        description="test tool",
        input_schema=_schema(["value"]),
        execute=execute or (lambda args, _ctx: {"value": args["value"]}),
        execution_mode=execution_mode,  # type: ignore[arg-type]
        parallel_safe=parallel_safe,
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


def test_registered_tool_receives_runtime_context_only_when_opted_in() -> None:
    seen: dict[str, Any] = {}

    def run(value: str, context: AgentToolContext) -> dict[str, Any]:
        seen["value"] = value
        seen["resource"] = context.resources["marker"]
        return {"ok": True}

    registered = RegisteredTool(
        name="contextual_echo",
        description="test registered tool",
        input_schema=_schema(["value"]),
        source="knowledge",
        run=run,
        accepts_runtime_context=True,
    )

    result = execute_tool_calls(
        [_call("contextual_echo", "abc")],
        [registered],
        {},
        tool_resources={"marker": "runtime"},
    )[0]

    assert result.is_error is False
    assert seen == {"value": "abc", "resource": "runtime"}


def test_registered_tool_without_context_opt_in_keeps_plain_run_contract() -> None:
    def run(value: str) -> dict[str, Any]:
        return {"value": value}

    registered = RegisteredTool(
        name="plain_registered_echo",
        description="test registered tool",
        input_schema=_schema(["value"]),
        source="knowledge",
        run=run,
    )

    result = execute_tool_calls(
        [_call("plain_registered_echo", "abc")],
        [registered],
        {},
        tool_resources={"marker": "runtime"},
    )[0]

    assert result.details == {"value": "abc"}


def test_injected_github_credentials_are_not_overridden_by_llm_args() -> None:
    seen: dict[str, Any] = {}

    def run(
        owner: str,
        repo: str,
        github_token: str | None = None,
        github_mode: str | None = None,
    ) -> dict[str, Any]:
        seen["github_token"] = github_token
        seen["github_mode"] = github_mode
        return {"ok": True}

    registered = RegisteredTool(
        name="github_probe",
        description="test github credential injection",
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "github_token": {"type": "string"},
                "github_mode": {"type": "string"},
            },
            "required": ["owner", "repo"],
        },
        source="github",
        run=run,
        extract_params=lambda sources: {
            "owner": sources["github"]["owner"],
            "repo": sources["github"]["repo"],
            "github_token": sources["github"]["auth_token"],
            "github_mode": sources["github"]["mode"],
        },
    )

    result = execute_tool_calls(
        [
            ToolCall(
                id="github-1",
                name="github_probe",
                input={
                    "owner": "wrong",
                    "repo": "wrong",
                    "github_token": "llm-token",
                    "github_mode": "metadata",
                },
            )
        ],
        [registered],
        {
            "github": {
                "connection_verified": True,
                "owner": "Tracer-Cloud",
                "repo": "opensre",
                "auth_token": "injected-token",
                "mode": "streamable-http",
            }
        },
    )[0]

    assert result.is_error is False
    assert seen == {
        "github_token": "injected-token",
        "github_mode": "streamable-http",
    }


def test_parallel_batch_preserves_provider_order() -> None:
    tools = [
        _tool("first", execute=lambda _args, _ctx: {"order": 1}),
        _tool("second", execute=lambda _args, _ctx: {"order": 2}),
    ]
    calls = [_call("second", "x"), _call("first", "x")]

    results = execute_tool_calls(calls, tools, {})

    assert [result.details for result in results] == [{"order": 2}, {"order": 1}]


def _registered_echo(name: str, *, parallel_safe: bool = True) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="test registered tool",
        input_schema=_schema(["value"]),
        source="knowledge",
        run=lambda value: {"value": value},
        parallel_safe=parallel_safe,
    )


def _record_pool_constructions(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    constructions: list[int] = []

    class _RecordingPool(ThreadPoolExecutor):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructions.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(core.execution, "ThreadPoolExecutor", _RecordingPool)
    return constructions


def test_all_parallel_safe_batch_goes_through_thread_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Control for the serialization tests below: proves the recording patch
    # observes pool construction, so their `constructions == []` assertions
    # cannot pass vacuously.
    constructions = _record_pool_constructions(monkeypatch)
    tools = [_tool("first"), _tool("second")]
    calls = [_call("first", "a"), _call("second", "b")]

    results = execute_tool_calls(calls, tools, {})

    assert constructions == [1]
    assert [result.details for result in results] == [{"value": "a"}, {"value": "b"}]


def test_non_parallel_safe_registered_tool_serializes_mixed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructions = _record_pool_constructions(monkeypatch)
    tools = [
        _tool("safe_one"),
        _tool("safe_two"),
        _registered_echo("stateful", parallel_safe=False),
    ]
    calls = [_call("safe_one", "a"), _call("stateful", "b"), _call("safe_two", "c")]

    results = execute_tool_calls(calls, tools, {})

    assert constructions == []
    assert [result.details for result in results] == [
        {"value": "a"},
        {"value": "b"},
        {"value": "c"},
    ]


def test_agent_tool_sequential_execution_mode_serializes_mixed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructions = _record_pool_constructions(monkeypatch)
    tools = [_tool("safe"), _tool("stateful", execution_mode="sequential")]
    calls = [_call("safe", "a"), _call("stateful", "b")]

    results = execute_tool_calls(calls, tools, {})

    assert constructions == []
    assert [result.details for result in results] == [{"value": "a"}, {"value": "b"}]


def test_agent_tool_parallel_safe_false_serializes_via_execution_mode_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No explicit execution_mode: effective_execution_mode must fall back to
    # parallel_safe and still force the whole batch sequential.
    constructions = _record_pool_constructions(monkeypatch)
    tools = [_tool("safe"), _tool("stateful", parallel_safe=False)]
    calls = [_call("safe", "a"), _call("stateful", "b")]

    results = execute_tool_calls(calls, tools, {})

    assert constructions == []
    assert [result.details for result in results] == [{"value": "a"}, {"value": "b"}]


def test_requires_sequential_execution_forces_serial_for_stateful_tools() -> None:
    tools = [
        _tool("safe"),
        _tool("sequential_agent", execution_mode="sequential"),
        _tool("unsafe_agent", parallel_safe=False),
        _registered_echo("unsafe_registered", parallel_safe=False),
    ]
    tool_map = {t.name: t for t in tools}

    # One sequential tool anywhere in the batch forces the whole batch.
    assert _requires_sequential_execution([_call("safe"), _call("sequential_agent")], tool_map)
    assert _requires_sequential_execution([_call("safe"), _call("unsafe_agent")], tool_map)
    assert _requires_sequential_execution([_call("safe"), _call("unsafe_registered")], tool_map)


def test_requires_sequential_execution_allows_parallel_otherwise() -> None:
    tools = [
        _tool("safe"),
        # Explicit execution_mode="parallel" overrides parallel_safe=False.
        _tool("override", execution_mode="parallel", parallel_safe=False),
        _registered_echo("safe_registered"),
    ]
    tool_map = {t.name: t for t in tools}

    assert not _requires_sequential_execution([], tool_map)
    assert not _requires_sequential_execution([_call("safe"), _call("safe_registered")], tool_map)
    assert not _requires_sequential_execution([_call("unknown_tool")], tool_map)
    assert not _requires_sequential_execution([_call("safe"), _call("override")], tool_map)


class _FakeLLM:
    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.model_id: str | None = None

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
