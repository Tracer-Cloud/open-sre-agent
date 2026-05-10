"""Classify interactive-shell input: slash, CLI help, agent, investigation, or follow-up."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from app.cli.interactive_shell.action_planner import plan_actions_with_unhandled
from app.cli.interactive_shell.intent_parser import (
    _damerau_levenshtein_distance,
    normalize_intent_text,
)
from app.cli.interactive_shell.terminal_intent import (
    is_sample_alert_launch_intent,
    mentions_alert_signal,
)

InputKind = Literal["slash", "cli_help", "cli_agent", "new_alert", "follow_up"]


class RoutingSession(Protocol):
    last_state: dict[str, object] | None


class RouteKind(StrEnum):
    SLASH = "slash"
    CLI_HELP = "cli_help"
    CLI_AGENT = "cli_agent"
    NEW_ALERT = "new_alert"
    FOLLOW_UP = "follow_up"


@dataclass(frozen=True)
class RouteDecision:
    route_kind: RouteKind
    confidence: float
    # Must contain only internal rule names; never user-derived content.
    matched_signals: tuple[str, ...] = ()
    fallback_reason: str | None = None

    def to_event_payload(self) -> dict[str, str | bool | int | float]:
        """Structured telemetry/debug payload for route decisions."""
        return {
            "route_kind": self.route_kind.value,
            "confidence": self.confidence,
            "matched_signals": ",".join(self.matched_signals),
            "fallback_reason": self.fallback_reason or "",
        }


@dataclass(frozen=True)
class RouteRule:
    name: str
    route_kind: RouteKind
    confidence: float
    matcher: Callable[[str, RoutingSession], bool]


def _is_slash_prefix(text: str, _session: RoutingSession) -> bool:
    return text.strip().startswith("/")


def _is_bare_command_alias(text: str, _session: RoutingSession) -> bool:
    stripped = text.strip()
    # Check exact (case-insensitive) match first so the typo corrector cannot
    # mis-correct a valid command word (e.g. "reset" → "test").
    if stripped.lower() in BARE_COMMAND_ALIASES:
        return True
    # Fall back to normalized form only for single-edit typos (distance ≤ 1).
    # Distance 2 matches too many unrelated words (e.g. "hello" → "help").
    normalized = normalize_intent_text(stripped)
    if normalized not in BARE_COMMAND_ALIASES:
        return False
    return _damerau_levenshtein_distance(stripped.lower(), normalized) <= 1


def _is_cli_help_rule(text: str, _session: RoutingSession) -> bool:
    return _is_cli_help_intent(text.strip())


def _is_sample_alert_rule(text: str, _session: RoutingSession) -> bool:
    return is_sample_alert_launch_intent(text.strip())


def _is_cli_agent_action_rule(
    text: str,
    _session: RoutingSession,
) -> bool:
    stripped = text.strip()
    actions, _unhandled = plan_actions_with_unhandled(stripped)
    if not actions:
        return False
    # Synthetic-test and sample-alert commands may contain incident vocabulary
    # in their scenario IDs (e.g. "002-connection-exhaustion"); allow them through
    # even when mentions_alert_signal fires on those words.
    if any(a.kind in {"synthetic_test", "sample_alert"} for a in actions):
        return True
    return not mentions_alert_signal(stripped)


def _is_new_alert_without_prior_state(
    text: str,
    session: RoutingSession,
) -> bool:
    return session.last_state is None and _reads_like_investigation_request(text.strip())


def _is_follow_up_with_prior_state(
    text: str,
    session: RoutingSession,
) -> bool:
    return session.last_state is not None and _is_short_question(text.strip())


def _is_new_alert_with_prior_state(
    text: str,
    session: RoutingSession,
) -> bool:
    return session.last_state is not None and _reads_like_investigation_request(text.strip())


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
        _is_bare_command_alias,
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
    RouteRule(
        "investigation_request",
        RouteKind.NEW_ALERT,
        0.86,
        _is_new_alert_without_prior_state,
    ),
    RouteRule(
        "short_follow_up_question",
        RouteKind.FOLLOW_UP,
        0.78,
        _is_follow_up_with_prior_state,
    ),
    RouteRule(
        "investigation_request_with_prior_state",
        RouteKind.NEW_ALERT,
        0.86,
        _is_new_alert_with_prior_state,
    ),
)


_MIN_INVESTIGATION_LINE_LEN = 48

# Bare words that map to slash commands; users often forget the leading slash.
# Keys without an explicit value rewrite to ``/<key>`` (e.g. ``help`` → ``/help``).
# Greetings and meta-words ("agent", "hi", "menu", …) all rewrite to ``/welcome``
# so a wandering user always lands on the structured welcome panel rather than a
# verbose, unstructured LLM reply. Greeting aliases are intentionally chosen to
# avoid conflicting Tab-completion prefixes with the existing command words
# (e.g. no ``hello`` because ``hel`` would no longer uniquely complete to ``help``).
_BARE_COMMAND_ALIAS_MAP: dict[str, str] = {
    "help": "/help",
    "?": "/help",
    "exit": "/exit",
    "quit": "/quit",
    "clear": "/clear",
    "reset": "/reset",
    "status": "/status",
    "trust": "/trust",
    "onboard": "/onboard",
    "deploy": "/deploy",
    "remote": "/remote",
    "tests": "/tests",
    "guardrails": "/guardrails",
    "update": "/update",
    "uninstall": "/uninstall",
    "agents": "/agents",
    "doctor": "/doctor",
    "welcome": "/welcome",
    "agent": "/welcome",
    "hi": "/welcome",
    "hey": "/welcome",
    "menu": "/welcome",
}
_BARE_COMMAND_ALIASES = frozenset(_BARE_COMMAND_ALIAS_MAP.keys())
BARE_COMMAND_ALIASES = _BARE_COMMAND_ALIASES
BARE_COMMAND_ALIAS_MAP = _BARE_COMMAND_ALIAS_MAP


# Short, question-shaped strings that obviously target the previous investigation.
_FOLLOW_UP_CUES = (
    "why",
    "how",
    "what",
    "was it",
    "is it",
    "explain",
    "tell me more",
    "more detail",
    "expand",
    "clarify",
)


# Extra vocabulary for short questions that describe production symptoms (not greetings).
_INCIDENT_QUESTION_WORDS = frozenset(
    {
        "slow",
        "database",
        "service",
        "pod",
        "deployment",
        "replica",
        "node",
        "cluster",
        "timeout",
        "latency",
        "throughput",
        "oom",
        "leak",
        "deadlock",
        "corrupt",
        "partial",
        "degraded",
    }
)
_INFORMATIONAL_QUESTION_RE = re.compile(
    r"^\s*(?:what(?!\s+caused?\b)|which|how\s+many)\b",
    re.IGNORECASE,
)
_INFORMATIONAL_QUESTION_WORDS = frozenset(
    {
        "available",
        "connect",
        "configured",
        "connected",
        "deployment",
        "deployments",
        "environment",
        "environments",
        "option",
        "options",
        "replica",
        "remote",
        "support",
        "supported",
    }
)

# Narrative signals for long pasted text; replaces "any line >=48 chars" with LangGraph.
_LONG_LINE_INCIDENT_RE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[45]\d{2}\b"),  # HTTP-style status codes
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:UTC|GMT|Z))?"),
    re.compile(r"\d+\s*%"),
    re.compile(r"\b(?:paged|on-?call|sev-?\d|SLO|SLA)\b", re.IGNORECASE),
)


def _long_line_suggests_incident_narrative(text: str) -> bool:
    """Long free text that looks like a production incident, not a how-to question."""
    if mentions_alert_signal(text):
        return True
    lower = text.lower()
    if any(rx.search(text) for rx in _LONG_LINE_INCIDENT_RE):
        return True
    # Plain-language incident narrative without a keyword in _ALERT_CUES
    return any(w in lower for w in ("failures", "failure", "outage", "degraded", "intermittent"))


_CLI_HELP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*how\s+do\s+i\s+run\s+(an?\s+)?(investigation|alert|rca)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*how\s+do\s+i\s+investigate\b", re.IGNORECASE),
    re.compile(
        r"^\s*how\s+do\s+i\s+(use|start|call|get|add|install|configure|invoke|check|list|"
        r"show|paste|submit|send|onboard|launch|open|deploy|integrate|connect|"
        r"set\s+up)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*how\s+to\s+(run|use|start|install|onboard|investigate|call|invoke|"
        r"configure|deploy|integrate|connect|set\s+up)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwhat\s+command\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+command\b", re.IGNORECASE),
    re.compile(
        r"^\s*where\s+do\s+i\s+(run|find|get|start|configure)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwalk\s+me\s+through\b", re.IGNORECASE),
    re.compile(
        r"\bshow\s+me\s+how\s+to\s+(run|use|start|install|onboard|configure|deploy|integrate)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwhat\s+does\s+opensre\b", re.IGNORECASE),
    re.compile(r"\b(list|available)\s+(of\s+)?commands\b", re.IGNORECASE),
    re.compile(r"\bsubcommand\b", re.IGNORECASE),
    # Documentation-style questions about features, integrations, and concepts.
    # These should ground in docs/ rather than relying on model memory (#1166).
    # The docs/documentation token is only a help signal when it appears with
    # question phrasing — bare mentions inside an incident description must
    # still route to the investigation pipeline.
    re.compile(
        r"\b(check|read|see|find|search|show|reference|consult|look\s+at)\s+"
        r"(the\s+)?(docs|documentation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(what|where|which)\s+(do|does|are|is)\s+(the\s+)?(docs|documentation)\b",
        re.IGNORECASE,
    ),
    # "according to the docs" / "per the docs" are citation phrasings — almost
    # exclusively used in docs questions, so no question-shape requirement.
    re.compile(
        r"\b(according\s+to|per)\s+(the\s+)?(docs|documentation)\b",
        re.IGNORECASE,
    ),
    # Bare "in (the) docs" is too broad on its own — incident text like
    # "the API errors are happening in docs" would otherwise short-circuit
    # the investigation pipeline. Only count it when the surrounding clause
    # is question-shaped (a `?` reachable without crossing a sentence
    # boundary).
    re.compile(
        r"\bin\s+(the\s+)?(docs|documentation)\b[^.!\n]*\?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*what\s+(is|are)\s+(\w+\s+){0,3}?(opensre|tracer|docs|documentation|"
        r"integrations?|features?|guardrails?|deployment|installation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*does\s+opensre\s+(support|have|integrate|work)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*can\s+(opensre|i)\s+(support|use|connect|integrate|configure|"
        r"deploy|install|run)\b",
        re.IGNORECASE,
    ),
)


def _is_short_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) >= 90:
        return False
    lower = stripped.lower()
    if stripped.endswith("?"):
        return True
    return any(lower.startswith(cue) for cue in _FOLLOW_UP_CUES)


def _looks_like_json_payload(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    else:
        return True


def _short_question_mentions_incident_vocab(text: str) -> bool:
    """True when a short question looks like a production issue, not small talk."""
    if not _is_short_question(text):
        return False
    lower = text.lower()
    if _INFORMATIONAL_QUESTION_RE.search(text) and any(
        word in lower for word in _INFORMATIONAL_QUESTION_WORDS
    ):
        return False
    if any(w in lower for w in _INCIDENT_QUESTION_WORDS):
        return True
    # "why is X failing" without a vocab hit still often means an incident.
    return any(v in lower for v in ("failing", "broken", "fails", "failed", "not working"))


def _reads_like_investigation_request(text: str) -> bool:
    """True when input should run the LangGraph investigation pipeline (not the CLI agent)."""
    stripped = text.strip()
    if not stripped:
        return False
    if _looks_like_json_payload(stripped):
        return True
    if len(stripped) >= _MIN_INVESTIGATION_LINE_LEN:
        return _long_line_suggests_incident_narrative(stripped)
    return mentions_alert_signal(stripped) or _short_question_mentions_incident_vocab(stripped)


def _is_cli_help_intent(text: str) -> bool:
    """True for meta-questions about how to use OpenSRE, the CLI, or the shell."""
    return any(pattern.search(text) for pattern in _CLI_HELP_PATTERNS)


def route_input(text: str, session: RoutingSession) -> RouteDecision:
    """Return a structured routing decision for interactive-shell input."""
    stripped = text.strip()

    for rule in ROUTE_RULES:
        if rule.matcher(stripped, session):
            return RouteDecision(
                route_kind=rule.route_kind,
                confidence=rule.confidence,
                matched_signals=(rule.name,),
            )

    if session.last_state is None:
        return RouteDecision(
            RouteKind.CLI_AGENT,
            0.45,
            (),
            "no_prior_investigation_and_no_incident_signal",
        )

    return RouteDecision(
        RouteKind.CLI_AGENT,
        0.45,
        (),
        "prior_investigation_but_no_follow_up_or_incident_signal",
    )


def classify_input(text: str, session: RoutingSession) -> InputKind:
    """Legacy InputKind adapter built on top of route_input()."""
    return route_input(text, session).route_kind.value


def slash_dispatch_text(text: str) -> str:
    """Return slash command text, including typo-tolerant bare alias mapping."""
    stripped = text.strip()
    if stripped.startswith("/"):
        return stripped
    normalized = normalize_intent_text(stripped)
    mapped = BARE_COMMAND_ALIAS_MAP.get(normalized)
    if mapped is not None:
        return mapped
    return f"/{stripped}"
