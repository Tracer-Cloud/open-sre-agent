"""High-level message routing pipeline for non-command input.

IMPORTANT ROUTING GUARDRAIL:
- Never add deterministic fallback classification for non-command text.
- Do not add regex-, keyword-, or rule-based non-command routing here.
- Non-command intent must come from the LLM classifier.
"""

from __future__ import annotations

from collections.abc import Callable

from app.cli.interactive_shell.orchestration import llm_intent_classifier
from app.cli.interactive_shell.routing.types import RouteDecision, RouteKind, RoutingSession


def llm_phase_route(
    text: str,
    session: RoutingSession,
) -> RouteDecision | None:
    """Resolve ambiguous routing input through the LLM classifier."""
    return llm_intent_classifier.classify_intent_with_llm(text, session)


def handle_message_with_agent(
    text: str,
    session: RoutingSession,
    *,
    llm_resolver: Callable[[str, RoutingSession], RouteDecision | None] = llm_phase_route,
) -> RouteDecision:
    """Resolve non-command input through the agent-facing LLM classifier."""
    try:
        llm_decision = llm_resolver(text, session)
        llm_failed = False
    except Exception:
        llm_decision = None
        llm_failed = True
    if llm_decision:
        return llm_decision

    # Policy guardrail: when the LLM cannot classify, never introduce
    # deterministic regex/keyword/rule fallback for non-command text.
    # Keep fallback neutral and route to cli_agent.
    return RouteDecision(
        RouteKind.CLI_AGENT,
        0.45,
        (),
        "llm_error_no_match" if llm_failed else "no_match",
    )
