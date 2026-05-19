"""Route rules and matchers for interactive-shell input classification."""

from __future__ import annotations

from app.cli.interactive_shell.intent.terminal_intent import (
    is_sample_alert_launch_intent,
    mentions_alert_signal,
)
from app.cli.interactive_shell.orchestration.action_planner import plan_actions_with_unhandled
from app.cli.interactive_shell.routing.aliases import is_bare_command_alias
from app.cli.interactive_shell.routing.docs_help_intents import is_docs_help_intent
from app.cli.interactive_shell.routing.types import RouteKind, RouteRule, RoutingSession

# Rule names that are part of the deterministic slash fast-path and must never
# be handed to the LLM classifier.
SLASH_FAST_PATH_RULE_NAMES: frozenset[str] = frozenset(
    {
        "slash_prefix",
        "bare_command_alias",
    }
)


def _is_slash_prefix(text: str, _session: RoutingSession) -> bool:
    return text.strip().startswith("/")


def _is_bare_command_alias_rule(text: str, _session: RoutingSession) -> bool:
    return is_bare_command_alias(text)


def _is_cli_help_rule(text: str, _session: RoutingSession) -> bool:
    return is_docs_help_intent(text.strip())


def _is_sample_alert_rule(text: str, _session: RoutingSession) -> bool:
    return is_sample_alert_launch_intent(text.strip())


def _is_cli_agent_action_rule(
    text: str,
    _session: RoutingSession,
) -> bool:
    stripped = text.strip()
    # Preserve docs/help routing for procedural "how/what command" questions.
    # Action-first routing should apply to executable intent, not to requests
    # asking for instructions.
    if is_docs_help_intent(stripped):
        return False
    actions, _unhandled = plan_actions_with_unhandled(stripped)
    if not actions:
        return False
    # Some deterministic action plans intentionally include investigation vocabulary
    # (e.g. quoted free-text investigation payloads). Allow those through as CLI
    # action plans instead of forcing NEW_ALERT routing.
    if any(action.kind in {"synthetic_test", "sample_alert", "investigation"} for action in actions):
        return True
    return not mentions_alert_signal(stripped)


ROUTE_RULES: tuple[RouteRule, ...] = (
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
    RouteRule(
        "cli_help_pattern",
        RouteKind.CLI_HELP,
        0.90,
        _is_cli_help_rule,
    ),
    RouteRule(
        "sample_alert_launch",
        RouteKind.CLI_AGENT,
        0.85,
        _is_sample_alert_rule,
    ),
    RouteRule(
        "cli_agent_action_plan",
        RouteKind.CLI_AGENT,
        0.83,
        _is_cli_agent_action_rule,
    ),
)

SLASH_FAST_PATH_RULES: tuple[RouteRule, ...] = tuple(
    rule for rule in ROUTE_RULES if rule.name in SLASH_FAST_PATH_RULE_NAMES
)
FALLBACK_RULES: tuple[RouteRule, ...] = tuple(
    rule for rule in ROUTE_RULES if rule.name not in SLASH_FAST_PATH_RULE_NAMES
)

# Backward-compat aliases while downstream imports migrate.
FAST_PATH_RULE_NAMES = SLASH_FAST_PATH_RULE_NAMES
FAST_PATH_RULES = SLASH_FAST_PATH_RULES

