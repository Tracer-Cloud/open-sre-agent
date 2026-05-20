"""Shared deterministic rule-matching helpers for routing phases."""

from __future__ import annotations

from app.cli.interactive_shell.routing.types import RouteDecision, RouteRule, RoutingSession


def decision_from_rule(rule: RouteRule, *, command_text: str | None = None) -> RouteDecision:
    """Convert a matched rule into the corresponding route decision."""
    return RouteDecision(
        route_kind=rule.route_kind,
        confidence=rule.confidence,
        matched_signals=(rule.name,),
        command_text=command_text,
    )


def first_matching_rule(
    text: str,
    session: RoutingSession,
    *,
    rules: tuple[RouteRule, ...],
) -> RouteRule | None:
    """Return the first rule whose matcher accepts the input and session."""
    for rule in rules:
        if rule.matcher(text, session):
            return rule
    return None
