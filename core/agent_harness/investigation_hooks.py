"""Bridge from ``ConnectedInvestigationAgent``'s subclass hooks to a ``SurfaceHooks`` bundle.

The investigation agent overrides ``Agent.run()`` entirely and its subclass
hooks (``build_tools``, ``build_system_prompt``) read from per-run state
populated at the top of ``run()``. Call ``investigation_agent_to_hooks`` while
a run is in progress; the projected hooks delegate live to the agent so the
prompt reflects current state each call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    from core.agent_harness.turn_context import TurnContext
    from core.types import RegisteredTool
    from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent


@dataclass(frozen=True)
class _InvestigationAgentHooks:
    """``SurfaceHooks`` bundle backed by a ``ConnectedInvestigationAgent``."""

    resolve_tools: ResolveToolsFn
    construct_prompt: ConstructPromptFn
    inject_context: InjectContextFn
    route_response: RouteResponseFn


def investigation_agent_to_hooks(
    investigation_agent: ConnectedInvestigationAgent,
) -> SurfaceHooks:
    """Return a ``SurfaceHooks`` bundle equivalent to ``investigation_agent``.

    ``resolve_tools`` calls ``ConnectedInvestigationAgent.build_tools()`` on demand.
    ``construct_prompt`` calls ``ConnectedInvestigationAgent.build_system_prompt``.
    ``inject_context`` and ``route_response`` are no-ops.
    The bundle is validated so a future refactor that drops a hook fails loud.
    """

    def _resolve_tools(_ctx: TurnContext) -> list[RegisteredTool]:
        return list(investigation_agent.build_tools())

    def _construct_prompt(_ctx: TurnContext) -> str:
        return investigation_agent.build_system_prompt()

    bundle = _InvestigationAgentHooks(
        resolve_tools=_resolve_tools,
        construct_prompt=_construct_prompt,
        inject_context=_default_inject_context,
        route_response=_default_route_response,
    )
    return assert_hooks_complete(bundle)
