"""Explicit per-surface initialization hooks for the agentic turn engine.

Every surface (interactive shell, headless CLI, Telegram gateway, Slack, …)
runs the same core loop in :mod:`core.agent`. What differs is *how* each
surface answers four questions the loop asks per turn:

1. **Resolve tools** — Which tools does this turn see?
2. **Construct prompt** — What system prompt does the LLM see?
3. **Inject context** — What extra fields ride on the provider request?
4. **Route response** — Where does the assistant's text go, and how are
   tool events reported?

Today each surface answers these inline in its entry function
(``interactive_shell/controller``, ``dispatch_message_to_headless_agent``,
``gateway/agent/dispatch_gateway_msg_to_agent``, and the four sites in
``core/agent_harness`` that call ``Agent(...)`` directly). Because the
answers live inside four unrelated call sites, new surfaces silently drop
fields — the reproducer is: connect Telegram to ``headless_agent`` and
tools never make it through.

:class:`SurfaceHooks` collects those four answers behind one named
Protocol so a surface either supplies all four or fails loud, and the
core loop calls the hooks in the order the four questions appear.

This module defines the contract only. Concrete adapter factories that
build a :class:`SurfaceHooks` from the existing
:mod:`core.agent_harness.ports` protocols live below; existing surfaces
migrate one hook at a time in the T-2b refactor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.agent_harness.ports import (
        ConfirmFn,
        OutputSink,
        ToolEventObserver,
        ToolProvider,
    )
    from core.agent_harness.turn_context import TurnContext
    from core.provider import ProviderRequest


ResolveToolsFn = Callable[["TurnContext"], list[Any]]
"""Return the concrete tools the agent may call this turn."""

ConstructPromptFn = Callable[["TurnContext"], str]
"""Return the system prompt for this turn.

Kept as ``str`` today to match the current ``Agent.__init__(system=...)``
call site; can widen to a ``PromptEnvelope`` in a later increment
without breaking the hook contract.
"""

InjectContextFn = Callable[["TurnContext", "ProviderRequest"], "ProviderRequest"]
"""Return a possibly-modified ``ProviderRequest`` before it reaches the LLM."""

RouteResponseFn = Callable[["TurnContext", str], None]
"""Deliver the assistant's final text to the surface.

Called once per turn *after* the core loop settles on ``final_text``.
Tool-call events are reported through
:class:`~core.execution.ToolExecutionHooks`, which surfaces continue to
implement per today's pattern.
"""


@runtime_checkable
class SurfaceHooks(Protocol):
    """The four explicit hooks every agent surface must supply.

    Each attribute is a callable. Protocols with callable attributes let
    surfaces implement the contract as either a dataclass of functions or
    a class with methods — both satisfy ``runtime_checkable``.
    """

    resolve_tools: ResolveToolsFn
    construct_prompt: ConstructPromptFn
    inject_context: InjectContextFn
    route_response: RouteResponseFn


class MissingHooksError(TypeError):
    """Raised when a surface passes a ``SurfaceHooks`` value that lacks one
    of the four required hooks.

    Kept as ``TypeError`` so callers that already handle "wrong shape"
    inputs catch it uniformly.
    """


def assert_hooks_complete(hooks: object) -> SurfaceHooks:
    """Validate ``hooks`` implements the full :class:`SurfaceHooks` contract.

    Returns ``hooks`` narrowed to :class:`SurfaceHooks` when complete;
    raises :class:`MissingHooksError` otherwise, listing the missing
    attributes so the caller (typically a surface entry function) sees
    which hook it forgot to wire.
    """
    required = ("resolve_tools", "construct_prompt", "inject_context", "route_response")
    missing = [name for name in required if not callable(getattr(hooks, name, None))]
    if missing:
        raise MissingHooksError(
            f"SurfaceHooks is missing required hook(s): {missing}. "
            "Every agent surface must supply all four; see "
            "core/agent_harness/surface_hooks.py for the contract."
        )
    return hooks  # type: ignore[return-value]


def _default_inject_context(_ctx: TurnContext, request: ProviderRequest) -> ProviderRequest:
    """No-op ``inject_context``.

    Kept as an explicit function (rather than an inline lambda) so tests
    and stack traces name it, and so future extension has one place to
    change without touching every call site.
    """
    return request


def _default_route_response(_ctx: TurnContext, _text: str) -> None:
    """No-op ``route_response`` — silently drops the assistant text.

    Suitable for headless / test surfaces that don't need the extra
    output channel. Real surfaces override this hook to print, stream,
    or forward the text to their end user.
    """
    return None


def hooks_from_ports(
    *,
    tool_provider: ToolProvider,
    output_sink: OutputSink | None = None,
    system_prompt: str = "",
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    inject_context: InjectContextFn | None = None,
    observer: ToolEventObserver | None = None,
) -> SurfaceHooks:
    """Build a :class:`SurfaceHooks` from the existing port objects.

    Bridges the T-1 ``ToolProvider``/``OutputSink`` protocols to the new
    hook contract so a surface can migrate incrementally: keep passing
    its existing ports, wrap them with this factory, and hand the
    resulting hooks to the loop.

    Args:
        tool_provider: The surface's existing tool provider port. Its
            ``action_tools(confirm_fn=..., is_tty=...)`` is called to
            answer ``resolve_tools``.
        output_sink: Optional output sink. When present,
            ``route_response`` prints the assistant text via
            ``output_sink.print``. Omit for headless surfaces that don't
            surface a channel.
        system_prompt: The system prompt this surface uses. Passed
            verbatim from ``construct_prompt``.
        confirm_fn, is_tty: Forwarded to ``tool_provider.action_tools``
            so the returned tools carry the surface's confirmation
            behaviour.
        inject_context: Optional override for the ``inject_context``
            hook. Defaults to the no-op.
        observer: Reserved for future use — surfaces observe tool events
            through ``ToolExecutionHooks``; kept as a parameter so
            callers can pre-thread one without an API change later.
    """

    def _resolve_tools(_ctx: TurnContext) -> list[Any]:
        return tool_provider.action_tools(confirm_fn=confirm_fn, is_tty=is_tty)

    def _construct_prompt(_ctx: TurnContext) -> str:
        return system_prompt

    def _route_response(_ctx: TurnContext, text: str) -> None:
        if output_sink is None or not text:
            return
        output_sink.print(text)

    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _HooksBundle:
        resolve_tools: ResolveToolsFn
        construct_prompt: ConstructPromptFn
        inject_context: InjectContextFn
        route_response: RouteResponseFn

    _ = observer  # parameter reserved for future use; keeps the API stable
    # A frozen dataclass whose fields are the four callables satisfies the
    # ``runtime_checkable`` Protocol structurally, but mypy's variance check
    # can't prove it because the field descriptors are ``property``-shaped at
    # runtime. Cast explicitly rather than widening the return annotation.
    bundle = _HooksBundle(
        resolve_tools=_resolve_tools,
        construct_prompt=_construct_prompt,
        inject_context=inject_context or _default_inject_context,
        route_response=_route_response,
    )
    return bundle  # type: ignore[return-value]
