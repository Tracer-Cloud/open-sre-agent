"""Deterministic slash fast-path routing phase evaluator."""

from __future__ import annotations

from app.cli.interactive_shell.routing.types import RouteDecision, RouteRule, RoutingSession
from app.cli.interactive_shell.routing.utils.matching import decision_from_rule, first_matching_rule


def slash_fast_path(
    text: str,
    session: RoutingSession,
    *,
    rules: tuple[RouteRule, ...],
) -> RouteDecision | None:
    """Return a route decision from slash fast-path rules, if any rule matches."""
    rule = first_matching_rule(text, session, rules=rules)
    if rule is None:
        return None
    return decision_from_rule(rule)


# Backward-compat alias while callers migrate.
fast_path = slash_fast_path
