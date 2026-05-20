"""Rule-based resolver for slash and bare-alias CLI command input."""

from __future__ import annotations

from app.cli.interactive_shell.routing.resolve_cli_command.matcher import (
    is_bare_command_alias,
    opensre_investigate_slash_text,
    slash_dispatch_text,
)
from app.cli.interactive_shell.routing.types import (
    RouteDecision,
    RouteKind,
    RouteRule,
    RoutingSession,
)
from app.cli.interactive_shell.routing.utils.matching import decision_from_rule, first_matching_rule


def _is_slash_prefix(text: str, _session: RoutingSession) -> bool:
    return text.strip().startswith("/")


def _is_bare_command_alias_rule(text: str, _session: RoutingSession) -> bool:
    return is_bare_command_alias(text)


CLI_COMMAND_RULES: tuple[RouteRule, ...] = (
    RouteRule(
        "slash_prefix",
        RouteKind.SLASH,
        1.0,
        _is_slash_prefix,
    ),
    RouteRule(
        "bare_command_alias",
        RouteKind.SLASH,
        0.98,
        _is_bare_command_alias_rule,
    ),
)


def resolve_cli_command(
    text: str,
    session: RoutingSession,
    *,
    rules: tuple[RouteRule, ...] = CLI_COMMAND_RULES,
) -> RouteDecision | None:
    """Return command-route decision for slash/bare-alias input, if matched."""
    investigate_slash = opensre_investigate_slash_text(text)
    if investigate_slash is not None:
        return RouteDecision(
            route_kind=RouteKind.SLASH,
            confidence=0.99,
            matched_signals=("opensre_investigate",),
            command_text=investigate_slash,
        )

    rule = first_matching_rule(text, session, rules=rules)
    if rule is None:
        return None
    return decision_from_rule(rule, command_text=slash_dispatch_text(text))
