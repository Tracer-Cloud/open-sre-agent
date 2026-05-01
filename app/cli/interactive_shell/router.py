"""Classify interactive-shell input: slash, CLI help, agent, investigation, or follow-up."""

from __future__ import annotations

import json
import re
from typing import Literal

from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.terminal_intent import (
    is_cli_agent_operational_intent,
    is_sample_alert_launch_intent,
    mentions_alert_signal,
)

InputKind = Literal["slash", "cli_help", "cli_agent", "new_alert", "follow_up"]

_MIN_INVESTIGATION_LINE_LEN = 48

# Bare words that map to slash commands; users often forget the leading slash.
_BARE_COMMAND_ALIASES = frozenset(
    {
        "help",
        "?",  # iconic shortcut for help, matches vim, less, many REPLs
        "exit",
        "quit",
        "clear",
        "reset",
        "status",
        "trust",
    }
)


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
        r"show|paste|submit|send|onboard|launch|open|set\s+up)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*how\s+to\s+(run|use|start|install|onboard|investigate|call|invoke)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwhat\s+command\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+command\b", re.IGNORECASE),
    re.compile(
        r"^\s*where\s+do\s+i\s+(run|find|get|start)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwalk\s+me\s+through\b", re.IGNORECASE),
    re.compile(
        r"\bshow\s+me\s+how\s+to\s+(run|use|start|install|onboard)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwhat\s+does\s+opensre\b", re.IGNORECASE),
    re.compile(r"\b(list|available)\s+(of\s+)?commands\b", re.IGNORECASE),
    re.compile(r"\bsubcommand\b", re.IGNORECASE),
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


def classify_input(text: str, session: ReplSession) -> InputKind:
    """Classify a single line of interactive-shell input.

    Rules (in order):
      1. Anything starting with ``/`` is a slash command.
      2. A bare word matching a known slash-command alias routes like slash.
      3. Procedural CLI questions route to ``cli_help`` (reference-grounded; no LangGraph).
      4. Sample-alert launch requests and local setup / health / list-integrations
         phrasing route to ``cli_agent`` (unless
         alert keywords indicate a real incident).
      5. With no prior investigation: if the line reads like an incident / alert /
         investigation request, route to ``new_alert`` (LangGraph). Otherwise route to
         ``cli_agent`` (LLM-only terminal assistant, no LangGraph).
      6. With a prior investigation: short question-shaped input about the RCA routes to
         ``follow_up``. New incident text routes to ``new_alert``. Otherwise route to
         ``cli_agent`` (chat / CLI help that is not an RCA follow-up).
    """
    stripped = text.strip()
    if stripped.startswith("/"):
        return "slash"

    if stripped.lower() in _BARE_COMMAND_ALIASES:
        return "slash"

    if _is_cli_help_intent(stripped):
        return "cli_help"

    if is_sample_alert_launch_intent(stripped):
        return "cli_agent"

    if is_cli_agent_operational_intent(stripped) and not mentions_alert_signal(stripped):
        return "cli_agent"

    if session.last_state is None:
        if _reads_like_investigation_request(stripped):
            return "new_alert"
        return "cli_agent"

    if _is_short_question(stripped):
        return "follow_up"

    if _reads_like_investigation_request(stripped):
        return "new_alert"

    return "cli_agent"
