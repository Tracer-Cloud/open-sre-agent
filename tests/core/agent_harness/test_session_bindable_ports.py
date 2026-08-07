"""Session/console/output bind ports are Protocol-visible for agent reuse."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_harness.ports import ConsoleBindable, OutputBindable, SessionBindable
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionStore,
    NullToolProvider,
    SimpleRunRecordFactory,
    StaticReasoningClientProvider,
)
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.host_cancel import ensure_turn_cancel


class _SpyTools:
    """Minimal SessionBindable + ConsoleBindable + ToolProvider for bind checks."""

    def __init__(self) -> None:
        self.sessions: list[Any] = []
        self.consoles: list[Any] = []

    def bind_session(self, session: Any) -> None:
        self.sessions.append(session)

    def bind_console(self, console: Any) -> None:
        self.consoles.append(console)

    def action_tools(self, **_kwargs: Any) -> list[Any]:
        return []

    def tool_resources(self) -> dict[str, Any]:
        return {}

    def observer(self, *, message: str) -> Any:
        _ = message

        def _observer(_kind: str, _data: dict[str, Any]) -> None:
            return None

        return _observer


def test_default_and_null_tool_providers_are_session_and_console_bindable() -> None:
    tools = DefaultToolProvider(InMemorySessionStore(), console=object())
    assert isinstance(tools, SessionBindable)
    assert isinstance(tools, ConsoleBindable)
    null = NullToolProvider()
    assert isinstance(null, SessionBindable)
    assert isinstance(null, ConsoleBindable)


def test_headless_default_ports_are_session_bindable() -> None:
    assert isinstance(EmptyPromptContextProvider(), SessionBindable)
    assert isinstance(StaticReasoningClientProvider(), SessionBindable)
    assert isinstance(SimpleRunRecordFactory(), SessionBindable)


def test_headless_agent_bind_session_invokes_session_bindable() -> None:
    first = InMemorySessionStore(session_id="a")
    second = InMemorySessionStore(session_id="b")
    tools = _SpyTools()
    agent = HeadlessAgent(tools=tools, session=first)
    agent.bind_session(second)
    assert tools.sessions == [second]


def test_headless_agent_bind_turn_console_invokes_console_bindable() -> None:
    tools = _SpyTools()
    agent = HeadlessAgent(tools=tools, session=InMemorySessionStore())
    agent.bind_turn(console="second")
    assert tools.consoles == ["second"]


def test_default_reasoning_provider_is_output_bindable() -> None:
    first = BufferOutputSink()
    second = BufferOutputSink()
    reasoning = DefaultReasoningClientProvider(output=first)
    assert isinstance(reasoning, OutputBindable)
    assert isinstance(StaticReasoningClientProvider(), OutputBindable)
    reasoning.bind_output(second)
    reasoning._handle_unavailable(RuntimeError("boom"), context="test")
    assert "LLM client unavailable" in second.text
    assert first.text == ""


def test_headless_agent_bind_turn_output_retargets_reasoning() -> None:
    first = BufferOutputSink()
    second = BufferOutputSink()
    reasoning = DefaultReasoningClientProvider(output=first)
    agent = HeadlessAgent(
        tools=NullToolProvider(),
        session=InMemorySessionStore(),
        output=first,
        reasoning=reasoning,
    )
    agent.bind_turn(output=second)
    reasoning._handle_unavailable(RuntimeError("boom"), context="test")
    assert "LLM client unavailable" in second.text
    assert first.text == ""


def test_ensure_turn_cancel_reuses_existing_event() -> None:
    class _Sink:
        turn_cancel = threading.Event()

    sink: Any = _Sink()
    first = ensure_turn_cancel(sink)
    second = ensure_turn_cancel(sink)
    assert first is second is sink.turn_cancel


def test_live_sink_exposes_turn_cancel_property() -> None:
    from gateway.core.runtime.live_sink import LiveOutputSink

    class _Inner:
        def __init__(self) -> None:
            self.turn_cancel = threading.Event()

        def print(self, message: str = "") -> None:
            _ = message

        def render_response_header(self, label: str) -> None:
            _ = label

        def render_error(self, message: str) -> None:
            _ = message

        def stream(self, *, label: str, chunks: Any, **_kwargs: Any) -> str:
            _ = (label, chunks)
            return ""

        def finalize(self, text: str) -> None:
            _ = text

    inner = _Inner()
    live = LiveOutputSink()
    assert live.turn_cancel is None
    live.bind(inner)  # type: ignore[arg-type]
    assert live.turn_cancel is inner.turn_cancel
