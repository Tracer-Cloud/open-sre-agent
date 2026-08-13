"""PostHog cohort / signup-retention identity for the unformed-metric floor.

Core asks *whether* a SessionGoal needs cohort identity and *whether* a reply
left it open; this module owns PostHog's markers, stop phrase, and event-name
rules. Registered via :func:`register_posthog_mcp_cohort_identity`.
"""

from __future__ import annotations

# Exact stop phrase the PostHog gather fragment tells the model to emit so the
# host floor / SessionGoal evaluate can key off a structured signal.
SIGNUP_EVENT_UNVERIFIED_MARK = "signup event unverified"

_SIGNUP_GOAL_MARKERS = (
    "signed up",
    "sign up",
    "sign-up",
    "signup",
)

_RETENTION_MARKER = "retention"

# Retention is a storage window in most product vocabulary (log / data /
# backup). Only a people-shaped question is about signups.
_USER_COHORT_MARKERS = (
    "user",
    "cohort",
    "person",
)

_LOGIN_EVENT_MARKERS = (
    "user_signed_in",
    "signed_in",
    "sign_in",
    "login",
)

_UNVERIFIED_REPLY_MARKERS = (
    "unverified",
    "unavailable",
    "unconfirmed",
    "recognized none",
    "could not identify",
    "cannot return a count",
    "cannot provide a retention",
    "cannot be calculated",
    "no eligible",
    "no signup",
    "no rows for event",
)


def posthog_goal_needs_cohort_identity(condition: str) -> bool:
    """True when a SessionGoal condition is a PostHog signup/retention ask."""
    text = (condition or "").casefold()
    if any(marker in text for marker in _SIGNUP_GOAL_MARKERS):
        return True
    if _RETENTION_MARKER not in text:
        return False
    return any(marker in text for marker in _USER_COHORT_MARKERS)


def posthog_reply_reports_cohort_unverified(text: str) -> bool:
    """True when the assistant reported that PostHog signup identity is open."""
    lower = (text or "").casefold()
    if SIGNUP_EVENT_UNVERIFIED_MARK in lower:
        return True
    if any(marker in lower for marker in _LOGIN_EVENT_MARKERS) and "signup" in lower:
        return True
    if "signup" in lower or "signed up" in lower or "retention" in lower:
        return any(marker in lower for marker in _UNVERIFIED_REPLY_MARKERS)
    return False


def register_posthog_mcp_cohort_identity() -> None:
    """Opt PostHog into core's cohort-identity ports."""
    from platform.harness_ports import (
        register_metric_cohort_goal_matcher,
        register_metric_cohort_unverified_detector,
    )

    register_metric_cohort_goal_matcher(posthog_goal_needs_cohort_identity)
    register_metric_cohort_unverified_detector(posthog_reply_reports_cohort_unverified)


__all__ = [
    "SIGNUP_EVENT_UNVERIFIED_MARK",
    "posthog_goal_needs_cohort_identity",
    "posthog_reply_reports_cohort_unverified",
    "register_posthog_mcp_cohort_identity",
]
