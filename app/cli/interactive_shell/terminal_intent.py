"""Reusable intent helpers for the OpenSRE interactive terminal."""

from __future__ import annotations

import re

from app.cli.support.constants import MANAGED_INTEGRATION_SERVICES

_ALERT_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\balert\b", re.IGNORECASE),
    re.compile(r"\berrors?\b", re.IGNORECASE),
    re.compile(r"\bfail(?:ure|ures|ing|ed|s)?\b", re.IGNORECASE),
    re.compile(r"\bdown\b", re.IGNORECASE),
    re.compile(r"\boutage\b", re.IGNORECASE),
    re.compile(r"\bspik(?:e|ed|ing)\b", re.IGNORECASE),
    re.compile(r"\bdropp(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\blatency\b", re.IGNORECASE),
    re.compile(r"\btimeouts?\b", re.IGNORECASE),
    re.compile(r"\b5xx\b", re.IGNORECASE),
    re.compile(r"\b50[03]\b", re.IGNORECASE),
    re.compile(r"\bcrash(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bcpu\b", re.IGNORECASE),
    re.compile(r"\bmemory\b", re.IGNORECASE),
    re.compile(r"\bdisk\b", re.IGNORECASE),
    re.compile(r"\bconnection\b", re.IGNORECASE),
    re.compile(r"\binvestigate\b", re.IGNORECASE),
)

_CLI_AGENT_OPERATIONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "run opensre health" style (also embedded in longer sentences)
    re.compile(r"\bopensre\s+health\b", re.IGNORECASE),
    re.compile(
        r"\b(check|verify)\s+(the\s+)?(health|status)\s+of\s+.{0,120}?\bopensre\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(check|verify)\s+.{0,40}?\bopensre\b.{0,80}?\b(health|status)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bshow\s+me\s+all\s+(connected\s+)?services\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(list|show)\s+(all\s+)?(my\s+)?(connected\s+)?(integrations?|services)\b",
        re.IGNORECASE,
    ),
)

_SAMPLE_ALERT_LAUNCH_RE = re.compile(
    r"\b(?:try|run|start|launch|fire|send|trigger)\b.{0,60}?"
    r"\b(?:sample|simple|test|demo)\s+(?:alert|event)\b",
    re.IGNORECASE,
)

_INTEGRATION_CONTEXT_RE = re.compile(
    r"\b(integrations?|services?|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)


def mentions_alert_signal(text: str) -> bool:
    """True when text contains production-incident vocabulary."""
    return any(pattern.search(text) for pattern in _ALERT_SIGNAL_PATTERNS)


def mentioned_integration_services(text: str) -> list[str]:
    """Return configured integration service names mentioned in user text."""
    lower = text.lower()
    services: list[str] = []
    for service in MANAGED_INTEGRATION_SERVICES:
        service_text = service.replace("_", " ")
        service_re = re.escape(service_text).replace(r"\ ", r"[\s_-]+")
        if re.search(rf"\b{service_re}\b", lower):
            services.append(service)
    return services


def is_integration_terminal_intent(text: str) -> bool:
    """True for questions about integrations/configured services, not incidents."""
    if not _INTEGRATION_CONTEXT_RE.search(text):
        return False
    return (
        bool(mentioned_integration_services(text))
        or re.search(r"\b(list|show|tell\s+me|get)\b", text, re.IGNORECASE) is not None
    )


def is_cli_agent_operational_intent(text: str) -> bool:
    """Setup / inventory questions that belong in the agentic terminal, not the graph."""
    return any(pattern.search(text) for pattern in _CLI_AGENT_OPERATIONAL_PATTERNS) or (
        is_integration_terminal_intent(text) and not mentions_alert_signal(text)
    )


def is_sample_alert_launch_intent(text: str) -> bool:
    """True when the user asks the shell to launch a built-in test alert."""
    return _SAMPLE_ALERT_LAUNCH_RE.search(text) is not None


__all__ = [
    "is_cli_agent_operational_intent",
    "is_integration_terminal_intent",
    "is_sample_alert_launch_intent",
    "mentioned_integration_services",
    "mentions_alert_signal",
]
