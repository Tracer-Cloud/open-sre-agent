"""Top-level interactive-shell router entrypoint.

THIS MODULE NEEDS TO BE A MAXIMUM OF 100 LINES OF CODE. IF IT IS NOT, YOU WILL BE FIRED.
REMOVE ANY CODE THAT IS NOT ESSENTIAL TO THE ROUTER.
This module intentionally exposes one orchestration function, :func:`route_input`,
and delegates all implementation details to sibling modules:

- ``aliases/``: bare command alias catalog, matching, and slash normalization
- ``docs_help_intents.py``: docs/help intent heuristics
- ``rules.py``: RouteRule definitions and fast/fallback rule groups
- ``fast_path/``: deterministic slash fast-path phase evaluator
- ``llm_phase/``: LLM phase evaluator plus classifier/resolver helpers
- ``regex_fallback/``: deterministic regex fallback phase evaluator
"""

from __future__ import annotations

from app.cli.interactive_shell.routing import aliases as routing_aliases
from app.cli.interactive_shell.routing.fast_path import slash_fast_path
from app.cli.interactive_shell.routing.llm_phase import llm_phase_route
from app.cli.interactive_shell.routing.regex_fallback import regex_fallback
from app.cli.interactive_shell.routing.rules import FALLBACK_RULES, SLASH_FAST_PATH_RULES
from app.cli.interactive_shell.routing.types import (
    RouteDecision,
    RouteKind,
    RoutingSession,
)

BARE_COMMAND_ALIASES = routing_aliases.BARE_COMMAND_ALIASES
BARE_COMMAND_ALIAS_MAP = routing_aliases.BARE_COMMAND_ALIAS_MAP


def route_input(text: str, session: RoutingSession) -> RouteDecision:
    """Return a structured routing decision for one interactive-shell turn.

    High-level pipeline:
    1. deterministic slash fast-path rules
    2. LLM classifier (ambiguous inputs only)
    3. deterministic regex fallback rules
    4. low-confidence default to ``cli_agent``
    """
    t = text.strip()

    fast_decision = slash_fast_path(t, session, rules=SLASH_FAST_PATH_RULES)
    if fast_decision:
        return fast_decision

    try:
        llm_decision = llm_phase_route(t, session)
        llm_failed = False
    except Exception:
        llm_decision = None
        llm_failed = True
    if llm_decision:
        return llm_decision

    fallback_decision = regex_fallback(t, session, rules=FALLBACK_RULES)
    if fallback_decision:
        return fallback_decision

    return RouteDecision(
        RouteKind.CLI_AGENT,
        0.45,
        (),
        "llm_error_no_match" if llm_failed else "no_match",
    )


def classify_input(text: str, session: RoutingSession) -> str:
    """Legacy InputKind adapter built on top of route_input()."""
    return route_input(text, session).route_kind.value


def is_bare_command_alias(text: str, _session: RoutingSession) -> bool:
    """True when ``text`` is a bare slash-command alias or accepted typo."""
    return routing_aliases.is_bare_command_alias(text)


def slash_dispatch_text(text: str) -> str:
    """Return slash command text, including typo-tolerant bare alias mapping."""
    return routing_aliases.slash_dispatch_text(text)


__all__ = [
    "BARE_COMMAND_ALIASES",
    "BARE_COMMAND_ALIAS_MAP",
    "RouteDecision",
    "RouteKind",
    "RoutingSession",
    "classify_input",
    "is_bare_command_alias",
    "route_input",
    "slash_dispatch_text",
]
