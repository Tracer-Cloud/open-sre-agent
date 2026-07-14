"""Business-impact scoring and issue classification for Sentry digests."""

from __future__ import annotations

from typing import Any

# Title keywords that signal operational blockers beyond raw event volume.
OPERATIONAL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("credentials", "blocks cloud/AWS credential-dependent workflows"),
    ("credit exhausted", "LLM billing or quota exhaustion"),
    ("stream failed", "investigation pipeline stream failure"),
    ("stopping pipeline", "investigation pipeline stopped"),
    ("unable to locate credentials", "missing cloud credentials"),
)


def classify_issue(issue: dict[str, Any]) -> str:
    if issue.get("regressedAt"):
        return "regression"
    if issue.get("status") == "new":
        return "new failure"
    return "ongoing"


def business_impact_score(issue: dict[str, Any]) -> tuple[int, list[str]]:
    """Score issues for priority ranking; higher is more urgent."""
    reasons: list[str] = []
    score = 0
    user_count = int(issue.get("userCount") or 0)
    event_count = int(issue.get("count") or 0)
    title = str(issue.get("title") or "").lower()

    if user_count:
        score += user_count * 100
        reasons.append(f"{user_count} users affected")

    for keyword, reason in OPERATIONAL_KEYWORDS:
        if keyword in title:
            score += 400
            reasons.append(reason)

    if issue.get("regressedAt"):
        score += 200
        reasons.append("regression resurfaced")

    if issue.get("status") == "new":
        score += 75
        reasons.append("new in this window")

    if event_count >= 50 and user_count == 0:
        penalty = min(event_count // 2, 250)
        score -= penalty
        reasons.append("high event volume with zero users — possible retry/noise")

    if event_count and not reasons:
        reasons.append(f"{event_count} events in window")

    return score, reasons
