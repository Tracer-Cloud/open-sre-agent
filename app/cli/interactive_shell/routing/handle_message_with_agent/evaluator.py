"""High-level message routing pipeline for non-command input."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
    ParseError,
    PlannerUnavailable,
    PolicyError,
    RoutingDegradeError,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_with_unhandled,
)
from app.cli.interactive_shell.routing.policy_tags import RouteSignal
from app.cli.interactive_shell.routing.types import RouteDecision, RouteKind, RoutingSession
from app.cli.support.exception_reporting import report_exception

_DEGRADE_CONFIDENCE = 0.42


def _degraded_route_decision(exc: RoutingDegradeError, *, text: str) -> RouteDecision:
    reason_tag = exc.reason_tag
    report_exception(
        exc,
        context="interactive_shell.routing.handle_message_with_agent",
        extra={
            "route_kind": RouteKind.CLI_AGENT.value,
            "degrade_reason_tag": reason_tag,
            "degrade_exception_class": type(exc).__name__,
            "text_length": len(text),
            "matched_signals": RouteSignal.CLI_AGENT_DEGRADED.value,
        },
    )
    return RouteDecision(
        RouteKind.CLI_AGENT,
        _DEGRADE_CONFIDENCE,
        (RouteSignal.CLI_AGENT_DEGRADED.value, reason_tag),
        reason_tag,
    )


def handle_message_with_agent(
    text: str,
    session: RoutingSession,
) -> RouteDecision:
    """Resolve non-command input to the CLI agent route."""
    _ = session

    try:
        mapped_actions, has_unhandled_clause = map_actions_with_unhandled(text)
    except (ParseError, PolicyError, PlannerUnavailable) as exc:
        return _degraded_route_decision(exc, text=text)

    matched_signals = (
        (RouteSignal.CLI_AGENT_ACTION_PLAN.value,)
        if mapped_actions and not has_unhandled_clause
        else ()
    )

    return RouteDecision(
        RouteKind.CLI_AGENT,
        0.88,
        matched_signals,
        None,
    )
