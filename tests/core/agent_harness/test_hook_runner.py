"""End-to-end tests for ``run_turn_via_hooks``.

Uses a fake LLM that returns a single no-tool-call response so the runner
completes one iteration and reports the final text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.hook_runner import run_turn_via_hooks
from core.agent_harness.surface_hooks import SurfaceHooks
from core.provider import ProviderRequest


@dataclass
class _FakeLLMResponse:
    """Minimal shape ``Agent.run`` needs from an LLM response."""

    content: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    raw_content: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class _FakeLLM:
    """LLM double: records requests and returns a scripted response.

    Only supports the two methods ``Agent.run`` calls: ``tool_schemas`` and
    ``invoke``.
    """

    response_text: str = "final answer"
    invocations: list[dict[str, Any]] = field(default_factory=list)
    model_id: str = "gpt-test"

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": getattr(t, "name", str(t))} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> _FakeLLMResponse:
        self.invocations.append({"messages": messages, "system": system, "tools": tools})
        return _FakeLLMResponse(content=self.response_text, tool_calls=[])

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[Any],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [
                {"id": tc.id, "name": tc.name, "output": output}
                for tc, output in zip(tool_calls, results)
            ],
        }


@dataclass(frozen=True)
class _Hooks:
    """Handwritten ``SurfaceHooks`` implementation for the tests."""

    resolve_tools: Any
    construct_prompt: Any
    inject_context: Any
    route_response: Any


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _identity_inject(_ctx: Any, request: ProviderRequest) -> ProviderRequest:
    return request


def _drop_response(_ctx: Any, _text: str) -> None:
    return None


# ---- Hooks reach the runner ----


def test_runner_calls_resolve_tools_and_passes_them_to_the_llm() -> None:
    fake_tools = [_FakeTool("grafana_query"), _FakeTool("datadog_logs")]

    def _resolve_tools(_ctx: Any) -> list[Any]:
        return fake_tools

    llm = _FakeLLM()
    hooks: SurfaceHooks = _Hooks(
        resolve_tools=_resolve_tools,
        construct_prompt=lambda _c: "",
        inject_context=_identity_inject,
        route_response=_drop_response,
    )

    run_turn_via_hooks(message="hello", hooks=hooks, llm=llm)

    assert llm.invocations, "LLM invoked at least once"
    tools_arg = llm.invocations[0]["tools"]
    assert [t["name"] for t in tools_arg] == ["grafana_query", "datadog_logs"]


def test_runner_calls_construct_prompt_and_passes_it_as_system() -> None:
    llm = _FakeLLM()
    hooks: SurfaceHooks = _Hooks(
        resolve_tools=lambda _c: [],
        construct_prompt=lambda _c: "SYSTEM PROMPT",
        inject_context=_identity_inject,
        route_response=_drop_response,
    )

    run_turn_via_hooks(message="hello", hooks=hooks, llm=llm)

    assert llm.invocations[0]["system"] == "SYSTEM PROMPT"


def test_runner_calls_inject_context_before_each_provider_request() -> None:
    """The hook must run for every iteration; here we run one, but a marker in
    the request proves the hook was applied end-to-end."""
    llm = _FakeLLM()

    def _tag_request(_ctx: Any, request: ProviderRequest) -> ProviderRequest:
        return ProviderRequest(
            messages=request.messages,
            system=(request.system or "") + " (injected)",
            tools=request.tools,
            metadata=request.metadata,
        )

    hooks: SurfaceHooks = _Hooks(
        resolve_tools=lambda _c: [],
        construct_prompt=lambda _c: "SYS",
        inject_context=_tag_request,
        route_response=_drop_response,
    )

    run_turn_via_hooks(message="hello", hooks=hooks, llm=llm)

    assert llm.invocations[0]["system"] == "SYS (injected)"


def test_runner_calls_route_response_with_the_final_text() -> None:
    delivered: list[tuple[Any, str]] = []

    def _route(ctx: Any, text: str) -> None:
        delivered.append((ctx, text))

    llm = _FakeLLM(response_text="the disk is full")
    hooks: SurfaceHooks = _Hooks(
        resolve_tools=lambda _c: [],
        construct_prompt=lambda _c: "",
        inject_context=_identity_inject,
        route_response=_route,
    )

    run_turn_via_hooks(message="why did it fail?", hooks=hooks, llm=llm, ctx=None)

    assert delivered == [(None, "the disk is full")]


# ---- Return value ----


def test_runner_returns_the_agent_run_result() -> None:
    llm = _FakeLLM(response_text="ok")
    hooks: SurfaceHooks = _Hooks(
        resolve_tools=lambda _c: [],
        construct_prompt=lambda _c: "",
        inject_context=_identity_inject,
        route_response=_drop_response,
    )

    result = run_turn_via_hooks(message="hello", hooks=hooks, llm=llm)

    assert result.final_text == "ok"
    assert result.executed == []


# ---- Validation ----


def test_runner_raises_when_hooks_are_incomplete() -> None:
    """A partial hook bundle must fail loud before any LLM call."""
    import pytest

    from core.agent_harness.surface_hooks import MissingHooksError

    @dataclass(frozen=True)
    class _Partial:
        # ``inject_context`` and ``route_response`` are missing.
        resolve_tools: Any = lambda _c: []
        construct_prompt: Any = lambda _c: ""

    llm = _FakeLLM()
    with pytest.raises(MissingHooksError):
        run_turn_via_hooks(message="hello", hooks=_Partial(), llm=llm)  # type: ignore[arg-type]

    assert llm.invocations == [], "LLM must not be called when hooks are incomplete"
