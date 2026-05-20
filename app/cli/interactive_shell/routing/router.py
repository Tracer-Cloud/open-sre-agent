"""Top-level interactive-shell router entrypoint.

This module intentionally exposes one orchestration function, :func:`route_input`,
and delegates all implementation details to sibling modules:

- ``command_route/``: deterministic slash command resolver and alias matcher
- ``message_route/``: LLM + fallback routing for non-command text
"""

from __future__ import annotations

from app.cli.interactive_shell.routing.command_route import resolve_cli_command
from app.cli.interactive_shell.routing.message_route import (
    handle_message_with_agent,
    llm_phase_route,
)
from app.cli.interactive_shell.routing.types import RouteDecision, RouteKind, RoutingSession


def route_input(text: str, session: RoutingSession) -> RouteDecision:
    """Return a structured routing decision for one interactive-shell turn."""
    # ROUTING CONTRACT (HARD INVARIANT):
    # Keep this entrypoint limited to the current two-branch shape:
    # 1) `resolve_cli_command(...)` for command-like input.
    # 2) `handle_message_with_agent(...)` for everything else.
    # Under no circumstance add new top-level routing branches or phases here.
    t = text.strip()
    cli_decision = resolve_cli_command(t, session)
    if cli_decision:
        return cli_decision
    # Guardrail: non-command routing must flow through the LLM classifier path.
    # Do not add deterministic regex/keyword classification at this layer.
    return handle_message_with_agent(
        t,
        session,
        llm_resolver=llm_phase_route,
    )


__all__ = [
    "RouteDecision",
    "RouteKind",
    "RoutingSession",
    "route_input",
]
