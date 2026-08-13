"""Signup / retention SessionGoal markers — string helpers only.

Leaf module: no imports from ``session_goal`` or ``metric_query_floor`` so
evaluate and the metric floor can share these without a cycle.
"""

from __future__ import annotations

# Exact stop phrase from the PostHog gather fragment — host floor keys off it.
SIGNUP_EVENT_UNVERIFIED_MARK = "signup event unverified"

# Host SessionGoal.condition tokens (explicit /goal set text — not free-chat
# intent routing around the action agent).
_SIGNUP_GOAL_MARKERS = (
    "signed up",
    "sign up",
    "sign-up",
    "signup",
    "retention",
)


def goal_condition_asks_signup_or_retention(condition: str) -> bool:
    """True when an attached SessionGoal condition is about signup/retention."""
    text = (condition or "").casefold()
    return any(marker in text for marker in _SIGNUP_GOAL_MARKERS)


def reply_reports_signup_unverified(text: str) -> bool:
    """True when the assistant reported that signup identity is unresolved."""
    lower = (text or "").casefold()
    if SIGNUP_EVENT_UNVERIFIED_MARK in lower:
        return True
    if "user_signed_in" in lower and "signup" in lower:
        return True
    markers = (
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
    if "signup" in lower or "signed up" in lower or "retention" in lower:
        return any(marker in lower for marker in markers)
    return False


__all__ = [
    "SIGNUP_EVENT_UNVERIFIED_MARK",
    "goal_condition_asks_signup_or_retention",
    "reply_reports_signup_unverified",
]
