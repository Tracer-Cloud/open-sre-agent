"""Top-level interactive-shell router entrypoint.

THIS MODULE NEEDS TO BE A MAXIMUM OF 100 LINES OF CODE. IF IT IS NOT, YOU WILL BE FIRED.
REMOVE ANY CODE THAT IS NOT ESSENTIAL TO THE ROUTER.

This module exposes one orchestration function, :func:`route_input`. Every turn
is handed to ``handle_message_with_agent``; the agent owns all behavior,
including a deterministic fast path that dispatches slash commands and aliases
without calling the LLM.
"""

from __future__ import annotations

from cli.interactive_shell.routing.types import (
    RouteDecision,
    RouteKind,
    RoutingSession,
)


def route_input(_text: str, _session: RoutingSession) -> RouteDecision:
    """Return the routing decision for one interactive-shell turn.

    ROUTING CONTRACT (HARD INVARIANT): this is a single-branch entrypoint. Every
    turn routes to ``handle_message_with_agent``. Do not add command/slash/help
    branches here; deterministic command dispatch is an internal fast path of
    the agent (see ``handle_message_with_agent``).
    """
    return RouteDecision(RouteKind.HANDLE_MESSAGE_WITH_AGENT, 1.0, (), None)


__all__ = [
    "RouteDecision",
    "RouteKind",
    "RoutingSession",
    "route_input",
]
