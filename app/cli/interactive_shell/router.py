"""Classify REPL input: slash, CLI help, LangGraph-free agent, investigation, or follow-up."""

from __future__ import annotations

import json
import re
from typing import Literal

from app.cli.interactive_shell.session import ReplSession

InputKind = Literal["slash", "cli_help", "cli_agent", "new_alert", "follow_up"]

_MIN_INVESTIGATION_LINE_LEN = 48

# Bare words that map to slash commands — users often forget the leading slash.
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


# Cues that strongly suggest a fresh incident rather than a follow-up.
_ALERT_CUES = (
    "alert",
    "error",
    "failure",
    "failing",
    "down",
    "outage",
    "spiked",
    "spike",
    "dropped",
    "latency",
    "timeout",
    "5xx",
    "500",
    "503",
    "crash",
    "crashed",
    "cpu",
    "memory",
    "disk",
    "connection",
    "investigate",
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

# Procedural / usage questions about OpenSRE — not production troubleshooting.
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


def _mentions_alert_signal(text: str) -> bool:
    lower = text.lower()
    return any(cue in lower for cue in _ALERT_CUES)


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
        return True
    return _mentions_alert_signal(stripped) or _short_question_mentions_incident_vocab(stripped)


def _is_cli_help_intent(text: str) -> bool:
    """True for meta-questions about how to use OpenSRE / the CLI / the REPL."""
    return any(pattern.search(text) for pattern in _CLI_HELP_PATTERNS)


def classify_input(text: str, session: ReplSession) -> InputKind:
    """Classify a single line of REPL input.

    Rules (in order):
      1. Anything starting with ``/`` is a slash command.
      2. A bare word matching a known slash-command alias routes like slash.
      3. Procedural CLI questions → ``cli_help`` (reference-grounded; no LangGraph).
      4. With no prior investigation: if the line reads like an incident / alert /
         investigation request → ``new_alert`` (LangGraph). Otherwise →
         ``cli_agent`` (LLM-only terminal assistant, no LangGraph).
      5. With a prior investigation: short question-shaped input about the RCA →
         ``follow_up``. New incident text → ``new_alert``. Otherwise →
         ``cli_agent`` (chat / CLI help that is not an RCA follow-up).
    """
    stripped = text.strip()
    if stripped.startswith("/"):
        return "slash"

    if stripped.lower() in _BARE_COMMAND_ALIASES:
        return "slash"

    if _is_cli_help_intent(stripped):
        return "cli_help"

    if session.last_state is None:
        if _reads_like_investigation_request(stripped):
            return "new_alert"
        return "cli_agent"

    if _is_short_question(stripped):
        return "follow_up"

    if _reads_like_investigation_request(stripped):
        return "new_alert"

    return "cli_agent"
