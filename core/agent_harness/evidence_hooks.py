"""Bridge from ``EvidenceAgent``'s subclass hooks to a ``SurfaceHooks`` bundle.

``EvidenceAgent`` uses subclass methods (``build_llm``, ``build_system_prompt``,
``build_tools``, ``user_message``) to customise the loop. This module returns a
``SurfaceHooks`` bundle backed by those methods so both patterns coexist while
call sites migrate. Import-only; no production wiring changes.
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
    from core.agent_harness.evidence_agent import EvidenceAgent
    from core.agent_harness.turn_context import TurnContext


@dataclass(frozen=True)
class _EvidenceAgentHooks:
    """``SurfaceHooks`` bundle backed by an ``EvidenceAgent``."""

    resolve_tools: ResolveToolsFn
    construct_prompt: ConstructPromptFn
    inject_context: InjectContextFn
    route_response: RouteResponseFn


def evidence_agent_to_hooks(evidence_agent: EvidenceAgent) -> SurfaceHooks:
    """Return a ``SurfaceHooks`` bundle equivalent to ``evidence_agent``.

    ``resolve_tools`` calls ``EvidenceAgent.build_tools()`` on demand so the
    lazy tool cache is honoured.
    ``construct_prompt`` calls ``EvidenceAgent.build_system_prompt``.
    ``inject_context`` and ``route_response`` are no-ops.
    The bundle is validated so a future refactor that drops a hook fails loud.
    """

    def _resolve_tools(_ctx: TurnContext) -> list[Any]:
        return list(evidence_agent.build_tools())

    def _construct_prompt(_ctx: TurnContext) -> str:
        return evidence_agent.build_system_prompt()

    bundle = _EvidenceAgentHooks(
        resolve_tools=_resolve_tools,
        construct_prompt=_construct_prompt,
        inject_context=_default_inject_context,
        route_response=_default_route_response,
    )
    return assert_hooks_complete(bundle)
