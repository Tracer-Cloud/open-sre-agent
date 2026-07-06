"""Shared harness for cross-surface turn parity tests.

Every client (interactive shell, headless dispatch, gateway turn handler) must
route through the same ``run_turn`` engine and produce the same outcome for the
same input, tools, and LLM wiring.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from rich.console import Console

from core.agent_harness.models.turn_results import ShellTurnResult
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.providers.default_providers import (
    DefaultReasoningClientProvider,
    DefaultToolProvider,
)
from core.agent_harness.session import InMemorySessionStorage, Session
from core.agent_harness.turns.headless_dispatch import (
    BufferOutputSink,
    NoopTurnAccounting,
    dispatch_message_to_headless_agent,
)
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool_framework.registered_tool import RegisteredTool
from gateway.turn_handler import build_gateway_turn_handler
from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

Surface = Literal["shell", "headless", "gateway_handler"]

ALL_SURFACES: tuple[Surface, ...] = (
    "shell",
    "headless",
    "gateway_handler",
)

PARITY_ANSWER = "PARITY_ANSWER"

_PROBE_RUNS: list[dict[str, Any]] = []
_INTEGRATIONS_SEEN: list[dict[str, Any]] = []


@dataclass(frozen=True)
class TurnSnapshot:
    """Normalized turn outcome used to compare surfaces."""

    final_intent: str
    action_handled: bool
    action_planned: int
    answered: bool
    assistant_text: str
    probe_ran: bool

    @classmethod
    def from_result(cls, result: ShellTurnResult, *, probe_ran: bool) -> TurnSnapshot:
        return cls(
            final_intent=result.final_intent,
            action_handled=result.action_result.handled,
            action_planned=result.action_result.planned_count,
            answered=result.answered,
            assistant_text=(result.assistant_response_text or "").strip(),
            probe_ran=probe_ran,
        )


def probe_tool() -> RegisteredTool:
    def _run(**kwargs: Any) -> dict[str, Any]:
        _PROBE_RUNS.append(kwargs)
        return {"status": "probe executed"}

    return RegisteredTool(
        name="parity_probe",
        description="Controlled action tool for cross-surface parity tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        source="knowledge",
        surfaces=("action",),
        run=_run,
        is_available=lambda _sources: True,
    )


def shell_run_tool(*, output: str = "parity-shell-ok") -> RegisteredTool:
    def _run(**kwargs: Any) -> dict[str, Any]:
        _PROBE_RUNS.append(kwargs)
        return {"stdout": output, "exit_code": 0, "response_text": output}

    return RegisteredTool(
        name="shell_run",
        description="Shell runner stub for bang-command parity tests.",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "additionalProperties": True,
        },
        source="knowledge",
        surfaces=("action",),
        run=_run,
        is_available=lambda _sources: True,
    )


def integration_gated_tool(*, integration: str = "slack") -> RegisteredTool:
    """Action tool that is only available when ``integration`` is in session sources."""

    tool_name = f"{integration}_parity_probe"

    def _run(**kwargs: Any) -> dict[str, Any]:
        _PROBE_RUNS.append({"integration": integration, **kwargs})
        return {"status": f"{integration} probe executed"}

    def _is_available(sources: dict[str, Any]) -> bool:
        item = sources.get(integration)
        if isinstance(item, dict):
            return bool(item.get("webhook_url") or item.get("connection_verified"))
        return bool(item)

    return RegisteredTool(
        name=tool_name,
        description=f"Integration-gated probe for {integration} parity tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        source=integration,
        surfaces=("action",),
        run=_run,
        is_available=_is_available,
    )


class FakeActionLLM:
    """Action-agent LLM: one tool call in ``tool`` mode, else text-only completion."""

    def __init__(self, mode: str, *, tool_name: str = "parity_probe") -> None:
        self._mode = mode
        self._tool_name = tool_name
        self._emitted = False

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": getattr(t, "name", str(t))} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: Any = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        if self._mode == "tool" and not self._emitted:
            self._emitted = True
            return AgentLLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name=self._tool_name, input={})],
                raw_content=None,
            )
        return AgentLLMResponse(content="done", tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": list(tool_calls)}

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        return {"role": "tool", "content": "[]", "results": list(zip(tool_calls, results))}


class FakeReasoningClient:
    """Answer/assistant agent: streams a canned reply."""

    def invoke_stream(self, _prompt: Any) -> Iterator[str]:
        yield PARITY_ANSWER


class RecordingGatewaySink:
    """Minimal gateway sink that records stream/finalize output for assertions."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.streamed: list[str] = []
        self.finalized: str | None = None

    def print(self, message: str = "") -> None:
        if message:
            self.lines.append(message)

    def render_response_header(self, label: str) -> None:
        self.lines.append(f"[{label}]")

    def render_error(self, message: str) -> None:
        self.lines.append(f"ERROR: {message}")

    def stream(
        self,
        *,
        label: str,
        chunks: Iterator[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(str(chunk) for chunk in chunks)
        self.streamed.append(text)
        return text

    def finalize(self, text: str) -> None:
        self.finalized = text

    @property
    def outbound_text(self) -> str:
        if self.finalized:
            return self.finalized.strip()
        if self.streamed:
            return self.streamed[-1].strip()
        return "\n".join(self.lines).strip()


def console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=100)


def fresh_session(*, integrations: dict[str, Any] | None = None) -> Session:
    session = Session(storage=InMemorySessionStorage())
    session.resolved_integrations_cache = dict(integrations or {})
    return session


def reset_probe_runs() -> None:
    _PROBE_RUNS.clear()


def reset_integrations_seen() -> None:
    _INTEGRATIONS_SEEN.clear()


def integrations_seen() -> list[dict[str, Any]]:
    return list(_INTEGRATIONS_SEEN)


def probe_run_count() -> int:
    return len(_PROBE_RUNS)


def wire_tool_registry(monkeypatch: Any, tools: list[RegisteredTool]) -> None:
    reset_probe_runs()
    reset_integrations_seen()
    monkeypatch.setattr(
        "core.agent_harness.tools.action_tools.get_surface_tools",
        lambda _surface: list(tools),
    )
    by_name = {tool.name: tool for tool in tools}
    monkeypatch.setattr(
        "core.agent_harness.tools.action_tools.get_surface_tool_map",
        lambda _surface: dict(by_name),
    )

    from core.agent_harness.tools.action_tools import _sources_for_context

    def _resolve_from_integrations(
        ctx: Any,
        *,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> list[RegisteredTool]:
        _INTEGRATIONS_SEEN.append(dict(resolved_integrations or {}))
        sources = _sources_for_context(ctx, resolved_integrations)
        return [tool for tool in tools if tool.is_available(sources)]

    monkeypatch.setattr(
        "core.agent_harness.providers.default_providers.get_action_tools_from_integrations_context",
        _resolve_from_integrations,
    )
    monkeypatch.setattr(
        "core.agent_harness.tools.action_tools.get_action_tools_from_integrations_context",
        _resolve_from_integrations,
    )


def wire_llms(
    monkeypatch: Any, *, action_mode: str, action_tool_name: str = "parity_probe"
) -> None:
    from core.llm.factory import LLMRole

    def _fake_get_llm(role: Any) -> Any:
        if role == LLMRole.AGENT:
            return FakeActionLLM(action_mode, tool_name=action_tool_name)
        return FakeReasoningClient()

    monkeypatch.setattr("core.llm.factory.get_llm", _fake_get_llm)


def _dispatch_turn(
    message: str,
    session: Session,
    *,
    gather_enabled: bool = True,
) -> ShellTurnResult:
    output = BufferOutputSink()
    return dispatch_message_to_headless_agent(
        message,
        tools=DefaultToolProvider(session, console()),
        session=session,
        output=output,
        prompts=DefaultPromptContextProvider(session),
        reasoning=DefaultReasoningClientProvider(output=output),
        accounting=NoopTurnAccounting(),
        gather_enabled=gather_enabled,
    )


def snapshot_shell(message: str, *, integrations: dict[str, Any] | None = None) -> TurnSnapshot:
    session = fresh_session(integrations=integrations)
    before = probe_run_count()
    result = execute_shell_turn(
        message,
        session,
        console(),
        recorder=None,
        is_tty=False,
    )
    return TurnSnapshot.from_result(result, probe_ran=probe_run_count() > before)


def snapshot_headless(message: str, *, integrations: dict[str, Any] | None = None) -> TurnSnapshot:
    session = fresh_session(integrations=integrations)
    before = probe_run_count()
    result = _dispatch_turn(message, session, gather_enabled=True)
    return TurnSnapshot.from_result(result, probe_ran=probe_run_count() > before)


def snapshot_gateway_handler(
    message: str,
    monkeypatch: Any,
    *,
    integrations: dict[str, Any] | None = None,
) -> TurnSnapshot:
    session = fresh_session(integrations=integrations)
    sink = RecordingGatewaySink()
    captured: list[ShellTurnResult] = []
    real_dispatch = dispatch_message_to_headless_agent

    def _spy(*args: Any, **kwargs: Any) -> ShellTurnResult:
        result = real_dispatch(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr("gateway.turn_handler.dispatch_message_to_headless_agent", _spy)
    before = probe_run_count()
    handler = build_gateway_turn_handler(console=console())
    handler(message, session, sink, logging.getLogger("test.parity.gateway"))
    assert len(captured) == 1, "gateway handler must dispatch exactly one headless turn"
    return TurnSnapshot.from_result(captured[0], probe_ran=probe_run_count() > before)


def run_surface(
    surface: Surface,
    message: str,
    monkeypatch: Any,
    *,
    integrations: dict[str, Any] | None = None,
) -> TurnSnapshot:
    if surface == "shell":
        return snapshot_shell(message, integrations=integrations)
    if surface == "headless":
        return snapshot_headless(message, integrations=integrations)
    if surface == "gateway_handler":
        return snapshot_gateway_handler(message, monkeypatch, integrations=integrations)
    raise AssertionError(f"unknown surface: {surface}")


def assert_surfaces_match(
    snapshots: dict[Surface, TurnSnapshot],
    *,
    reference: Surface = "shell",
) -> None:
    ref = snapshots[reference]
    for surface, snap in snapshots.items():
        assert snap == ref, (
            f"surface {surface!r} diverged from {reference!r}:\n"
            f"  reference: {ref}\n"
            f"  actual:    {snap}"
        )


def collect_all_surfaces(
    message: str,
    monkeypatch: Any,
    *,
    integrations: dict[str, Any] | None = None,
) -> dict[Surface, TurnSnapshot]:
    snapshots: dict[Surface, TurnSnapshot] = {}
    for surface in ALL_SURFACES:
        snapshots[surface] = run_surface(
            surface,
            message,
            monkeypatch,
            integrations=integrations,
        )
    return snapshots


def run_gateway_turn_with_sink(
    message: str,
    monkeypatch: Any,
    *,
    integrations: dict[str, Any] | None = None,
) -> tuple[TurnSnapshot, RecordingGatewaySink]:
    """Run one gateway turn and return both routing snapshot and transport sink."""
    session = fresh_session(integrations=integrations)
    sink = RecordingGatewaySink()
    captured: list[ShellTurnResult] = []
    real_dispatch = dispatch_message_to_headless_agent

    def _spy(*args: Any, **kwargs: Any) -> ShellTurnResult:
        result = real_dispatch(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr("gateway.turn_handler.dispatch_message_to_headless_agent", _spy)
    before = probe_run_count()
    handler = build_gateway_turn_handler(console=console())
    handler(message, session, sink, logging.getLogger("test.parity.gateway.sink"))
    assert len(captured) == 1, "gateway handler must dispatch exactly one headless turn"
    snapshot = TurnSnapshot.from_result(captured[0], probe_ran=probe_run_count() > before)
    return snapshot, sink
