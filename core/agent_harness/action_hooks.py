"""Bridge from ``ActionAgent``'s subclass hooks to a ``SurfaceHooks`` bundle.

``ActionAgent`` uses subclass methods (``build_llm``, ``build_system_prompt``,
``user_message``) to customise the loop. This module returns a ``SurfaceHooks``
bundle backed by those methods so both patterns coexist while call sites
migrate. Import-only; no production wiring changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.agent_harness.surface_hooks import (
    ConstructPromptFn,
    InjectContextFn,
    ResolveToolsFn,
    RouteResponseFn,
    SurfaceHooks,
    _default_inject_context,
    _default_route_response,
    assert_hooks_complete,
)

if TYPE_CHECKING:
    from core.agent_harness.action_agent import ActionAgent
    from core.agent_harness.turn_context import TurnContext


@dataclass(frozen=True)
class _ActionAgentHooks:
    """``SurfaceHooks`` bundle backed by an ``ActionAgent``."""

    resolve_tools: ResolveToolsFn
    construct_prompt: ConstructPromptFn
    inject_context: InjectContextFn
    route_response: RouteResponseFn


def action_agent_to_hooks(action_agent: ActionAgent) -> SurfaceHooks:
    """Return a ``SurfaceHooks`` bundle equivalent to ``action_agent``.

    ``resolve_tools`` returns the tool list ``ActionAgent`` captured during
    ``__init__`` (reflects the bang/slash short-circuit).
    ``construct_prompt`` calls ``ActionAgent.build_system_prompt``.
    ``inject_context`` and ``route_response`` are no-ops — ``ActionAgent`` doesn't
    modify the provider request, and output goes through the wrapper.
    The bundle is validated so a future refactor that drops a hook fails loud.
    """
    captured_tools: list[Any] = list(action_agent._tools or [])  # noqa: SLF001

    def _resolve_tools(_ctx: TurnContext) -> list[Any]:
        # A shallow copy: callers must not mutate the agent's captured list.
        return list(captured_tools)

    def _construct_prompt(_ctx: TurnContext) -> str:
        return action_agent.build_system_prompt()

    bundle = _ActionAgentHooks(
        resolve_tools=_resolve_tools,
        construct_prompt=_construct_prompt,
        inject_context=_default_inject_context,
        route_response=_default_route_response,
    )
    return assert_hooks_complete(bundle)
