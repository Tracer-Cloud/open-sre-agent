"""Top-level interactive-shell router entrypoint.

THIS MODULE NEEDS TO BE A MAXIMUM OF 100 LINES OF CODE. IF IT IS NOT, YOU WILL BE FIRED.
REMOVE ANY CODE THAT IS NOT ESSENTIAL TO THE ROUTER.
This module intentionally exposes one orchestration function, :func:`route_input`,
and delegates all implementation details to sibling modules:

- ``resolve_cli_command/``: deterministic slash command resolver and alias matcher
- ``handle_message_with_agent/``: LLM + fallback routing for non-command text
"""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.evaluator import (
    handle_message_with_agent,
    llm_phase_route,
)
from app.cli.interactive_shell.routing.resolve_cli_command.evaluator import (
    resolve_cli_command,
)
from app.cli.interactive_shell.routing.types import (
    RouteDecision,
    RouteKind,
    RoutingSession,
)


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
    return handle_message_with_agent(
        t,
        session,
        llm_resolver=llm_phase_route,
    )


def classify_input(text: str, session: RoutingSession) -> str:
    """Legacy InputKind adapter built on top of route_input()."""
    return route_input(text, session).route_kind.value


__all__ = [
    "RouteDecision",
    "RouteKind",
    "RoutingSession",
    "classify_input",
    "route_input",
]
