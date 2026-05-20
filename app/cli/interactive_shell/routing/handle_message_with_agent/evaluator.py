"""High-level message routing pipeline for non-command input."""

from __future__ import annotations

from collections.abc import Callable

from app.cli.interactive_shell.routing import llm_intent_classifier
from app.cli.interactive_shell.routing.types import RouteDecision, RouteKind, RoutingSession


def _looks_like_cli_agent_action_plan(text: str) -> bool:
    lowered = text.lower()
    return (
        "run synthetic test" in lowered
        or "deploy" in lowered
        or "connected services" in lowered
        or "switch to " in lowered
    )


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

    matched_signals = ("cli_agent_action_plan",) if _looks_like_cli_agent_action_plan(text) else ()

    return RouteDecision(
        RouteKind.CLI_AGENT,
        0.45,
        matched_signals,
        "llm_error_no_match" if llm_failed else "no_match",
    )
