"""Reusable intent helpers for the OpenSRE interactive terminal."""

from __future__ import annotations

import re

from app.cli.interactive_shell.intent_parser import SAMPLE_ALERT_RE
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


# Consolidated: sample-alert launch detection now delegates to the canonical
# SAMPLE_ALERT_RE defined in intent_parser, which is the single source of truth
# shared by both the routing surface and the action-planner surface.
# The local _SAMPLE_ALERT_LAUNCH_RE pattern was identical and has been removed
# as part of the typed-routing consolidation (see #1375 / #1378).


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


def is_sample_alert_launch_intent(text: str) -> bool:
    """True when the user asks the shell to launch a built-in test alert.

    Delegates to ``SAMPLE_ALERT_RE`` from ``intent_parser``, which is the
    single canonical pattern shared with the action-planning surface.
    """
    return SAMPLE_ALERT_RE.search(text) is not None


__all__ = [
    "is_sample_alert_launch_intent",
    "mentioned_integration_services",
    "mentions_alert_signal",
]
