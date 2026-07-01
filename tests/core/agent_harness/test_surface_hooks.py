"""Tests for the ``SurfaceHooks`` contract and factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.agent_harness.surface_hooks import (
    MissingHooksError,
    SurfaceHooks,
    assert_hooks_complete,
    hooks_from_ports,
)


@dataclass
class _RecordingToolProvider:
    """Minimal ``ToolProvider`` — records inputs so tests can assert wiring."""

    tools_to_return: list[Any] = field(default_factory=list)
    action_tools_called_with: list[dict[str, Any]] = field(default_factory=list)

    def action_tools(self, *, confirm_fn: Any, is_tty: bool | None) -> list[Any]:
        self.action_tools_called_with.append({"confirm_fn": confirm_fn, "is_tty": is_tty})
        return list(self.tools_to_return)

    def tool_resources(self) -> dict[str, Any]:
        return {}

    def observer(self, *, message: str) -> Any:
        _ = message
        return lambda _kind, _data: None


@dataclass
class _RecordingOutputSink:
    """Minimal ``OutputSink`` — records what was printed."""

    printed: list[str] = field(default_factory=list)

    def print(self, message: str = "") -> None:
        self.printed.append(message)

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        _ = message

    def stream(self, *, label: str, chunks: Any, suppress_if_starts_with: str | None = None) -> str:
        _ = (label, chunks, suppress_if_starts_with)
        return ""


class _StubTurnContext:
    """Test double for TurnContext."""


# ---- assert_hooks_complete ----


def test_assert_hooks_complete_accepts_a_fully_wired_hook_bundle() -> None:
    ctx = _StubTurnContext()

    @dataclass(frozen=True)
    class _FullHooks:
        resolve_tools: Any = lambda _c: []
        construct_prompt: Any = lambda _c: ""
        inject_context: Any = lambda _c, req: req
        route_response: Any = lambda _c, _t: None

    hooks = _FullHooks()
    assert assert_hooks_complete(hooks) is hooks
    assert isinstance(hooks, SurfaceHooks)
    assert hooks.resolve_tools(ctx) == []
    assert hooks.construct_prompt(ctx) == ""


def test_assert_hooks_complete_raises_with_the_specific_missing_hook_named() -> None:
    """The validator names the missing hook so the surface fails loud."""

    @dataclass(frozen=True)
    class _PartialHooks:
        construct_prompt: Any = lambda _c: ""
        inject_context: Any = lambda _c, req: req
        route_response: Any = lambda _c, _t: None

    with pytest.raises(MissingHooksError, match="resolve_tools"):
        assert_hooks_complete(_PartialHooks())


def test_assert_hooks_complete_lists_multiple_missing_hooks() -> None:
    """All missing hooks are reported at once, not just the first."""

    @dataclass(frozen=True)
    class _BarelyPresent:
        route_response: Any = lambda _c, _t: None

    with pytest.raises(MissingHooksError) as excinfo:
        assert_hooks_complete(_BarelyPresent())
    msg = str(excinfo.value)
    assert "resolve_tools" in msg
    assert "construct_prompt" in msg
    assert "inject_context" in msg


def test_assert_hooks_complete_rejects_non_callable_hooks() -> None:
    """A hook typed as a plain value (not a function) fails validation."""

    @dataclass(frozen=True)
    class _NonCallable:
        resolve_tools: Any = None  # oops — a value, not a function
        construct_prompt: Any = lambda _c: ""
        inject_context: Any = lambda _c, req: req
        route_response: Any = lambda _c, _t: None

    with pytest.raises(MissingHooksError, match="resolve_tools"):
        assert_hooks_complete(_NonCallable())


# -----------------------------------------------------------------------------
# hooks_from_ports — the compatibility bridge from ports to hooks
# -----------------------------------------------------------------------------


def test_hooks_from_ports_returns_a_complete_hook_bundle() -> None:
    provider = _RecordingToolProvider()
    hooks = hooks_from_ports(tool_provider=provider)
    assert assert_hooks_complete(hooks) is hooks
    assert isinstance(hooks, SurfaceHooks)


def test_hooks_from_ports_resolve_tools_forwards_confirm_and_tty_to_the_provider() -> None:
    """confirm_fn and is_tty must reach the provider so tools carry the right UX."""
    provider = _RecordingToolProvider(tools_to_return=[{"name": "tool_a"}])
    sentinel_confirm = object()
    hooks = hooks_from_ports(tool_provider=provider, confirm_fn=sentinel_confirm, is_tty=True)

    tools = hooks.resolve_tools(_StubTurnContext())

    assert tools == [{"name": "tool_a"}]
    assert provider.action_tools_called_with == [{"confirm_fn": sentinel_confirm, "is_tty": True}]


def test_hooks_from_ports_construct_prompt_returns_the_passed_system_prompt() -> None:
    hooks = hooks_from_ports(
        tool_provider=_RecordingToolProvider(),
        system_prompt="Test system prompt.",
    )
    assert hooks.construct_prompt(_StubTurnContext()) == "Test system prompt."


def test_hooks_from_ports_construct_prompt_defaults_to_empty_string() -> None:
    """The factory has no default prompt — the surface owns it."""
    hooks = hooks_from_ports(tool_provider=_RecordingToolProvider())
    assert hooks.construct_prompt(_StubTurnContext()) == ""


def test_hooks_from_ports_inject_context_defaults_to_identity() -> None:
    """The default hook returns the request unchanged."""
    hooks = hooks_from_ports(tool_provider=_RecordingToolProvider())
    request = object()
    assert hooks.inject_context(_StubTurnContext(), request) is request  # type: ignore[arg-type]


def test_hooks_from_ports_inject_context_uses_override_when_provided() -> None:
    """An override callable is honoured."""

    calls: list[tuple[Any, Any]] = []

    def _tagging_inject(_ctx: Any, req: Any) -> Any:
        calls.append((_ctx, req))
        return {"wrapped": req}

    hooks = hooks_from_ports(
        tool_provider=_RecordingToolProvider(),
        inject_context=_tagging_inject,
    )

    ctx = _StubTurnContext()
    request = {"messages": []}
    out = hooks.inject_context(ctx, request)  # type: ignore[arg-type]

    assert out == {"wrapped": {"messages": []}}
    assert calls == [(ctx, {"messages": []})]


def test_hooks_from_ports_route_response_writes_to_the_output_sink() -> None:
    sink = _RecordingOutputSink()
    hooks = hooks_from_ports(
        tool_provider=_RecordingToolProvider(),
        output_sink=sink,
    )
    hooks.route_response(_StubTurnContext(), "hello")
    assert sink.printed == ["hello"]


def test_hooks_from_ports_route_response_is_noop_when_no_output_sink() -> None:
    """Headless surfaces have no channel; the hook must not raise."""
    hooks = hooks_from_ports(tool_provider=_RecordingToolProvider())
    hooks.route_response(_StubTurnContext(), "silent")


def test_hooks_from_ports_route_response_skips_empty_text() -> None:
    """Empty text doesn't reach the sink."""
    sink = _RecordingOutputSink()
    hooks = hooks_from_ports(
        tool_provider=_RecordingToolProvider(),
        output_sink=sink,
    )
    hooks.route_response(_StubTurnContext(), "")
    assert sink.printed == []
