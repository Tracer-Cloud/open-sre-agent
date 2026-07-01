"""Per-surface initialization hooks for the agent turn loop.

Every surface (interactive shell, headless, gateway, …) runs the same
core loop but answers four questions differently per turn: which tools
the turn sees, what system prompt the LLM sees, whether the provider
request needs extra fields, and where the assistant's text goes.

Today those four answers are inlined at each surface entry, so a new
surface can silently drop fields. This module collects the four answers
behind one Protocol so a surface either supplies all four or fails loud.
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
"""Return the tools the agent may call this turn."""

ConstructPromptFn = Callable[["TurnContext"], str]
"""Return the system prompt for this turn."""

InjectContextFn = Callable[["TurnContext", "ProviderRequest"], "ProviderRequest"]
"""Return a possibly-modified provider request before it reaches the LLM."""

RouteResponseFn = Callable[["TurnContext", str], None]
"""Deliver the assistant's final text to the surface, once per turn."""


@runtime_checkable
class SurfaceHooks(Protocol):
    """The four hooks every agent surface must supply."""

    resolve_tools: ResolveToolsFn
    construct_prompt: ConstructPromptFn
    inject_context: InjectContextFn
    route_response: RouteResponseFn


class MissingHooksError(TypeError):
    """Raised when a surface hooks value is missing one of the four hooks."""


def assert_hooks_complete(hooks: object) -> SurfaceHooks:
    """Return ``hooks`` if all four hooks are callable, else raise
    :class:`MissingHooksError` naming the missing ones."""
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

    A named function (not a lambda) so tests and stack traces name it.
    """
    return request


def _default_route_response(_ctx: TurnContext, _text: str) -> None:
    """Drops the assistant text — headless surfaces have no channel."""
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

    ``resolve_tools`` calls ``tool_provider.action_tools(...)``.
    ``construct_prompt`` returns ``system_prompt`` verbatim.
    ``inject_context`` defaults to the identity; pass an override to enrich the request.
    ``route_response`` prints via ``output_sink`` when one is supplied.
    ``observer`` is reserved for a later increment and currently unused.
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

    _ = observer  # reserved for a later increment
    # The dataclass satisfies the Protocol structurally but mypy can't prove
    # variance on callable-typed fields; cast at the boundary.
    bundle = _HooksBundle(
        resolve_tools=_resolve_tools,
        construct_prompt=_construct_prompt,
        inject_context=inject_context or _default_inject_context,
        route_response=_route_response,
    )
    return bundle  # type: ignore[return-value]
