"""Compact Sentry issue digests for model context budgets."""

from __future__ import annotations

import re
from typing import Any

# Mirrors default buckets in integrations/sentry/tools/skills/sentry-summary/SKILL.md
_CLUSTER_RULES: tuple[tuple[str, str], ...] = (
    ("Auth / API key errors", r"\b(auth|api[_ -]?key|token|oauth|unauthorized|forbidden|401|403)\b"),
    (
        "Windows / OS install failures",
        r"\b(windows|win32|setup\.exe|installer|install fail)",
    ),
    (
        "Backend timeouts or connection errors",
        r"\b(timeout|timed out|connection refused|connection error|backend|ingest|database|db )\b",
    ),
    ("Frontend / UI crashes", r"\b(frontend|react|browser|ui crash|client error)\b"),
    ("CI / pipeline failures", r"\b(ci |pipeline|github action|workflow|jenkins)\b"),
)

_TOP_ISSUE_LIMIT = 5


def _issue_text(issue: dict[str, Any]) -> str:
    metadata = issue.get("metadata")
    meta_text = ""
    if isinstance(metadata, dict):
        meta_text = " ".join(str(value) for value in metadata.values() if value)
    return " ".join(
        str(issue.get(field, "") or "")
        for field in ("title", "culprit", meta_text)
    ).lower()


def cluster_name_for_issue(issue: dict[str, Any]) -> str:
    """Assign one default theme bucket for an issue."""
    text = _issue_text(issue)
    for name, pattern in _CLUSTER_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return name
    return "Other / uncategorised"


def classify_issue(issue: dict[str, Any], cluster: str) -> str:
    if issue.get("regressedAt"):
        return "regression"
    if cluster == "Auth / API key errors":
        return "auth"
    if issue.get("status") == "new":
        return "new failure"
    return "ongoing"


def _impact_score(issue: dict[str, Any]) -> tuple[int, int]:
    user_count = int(issue.get("userCount") or 0)
    event_count = int(issue.get("count") or 0)
    return (user_count, event_count)


def slim_issue(issue: dict[str, Any], *, cluster: str, classification: str) -> dict[str, Any]:
    return {
        "id": issue.get("id"),
        "short_id": issue.get("shortId") or issue.get("id"),
        "title": issue.get("title"),
        "culprit": issue.get("culprit"),
        "cluster": cluster,
        "classification": classification,
        "count": issue.get("count"),
        "user_count": issue.get("userCount"),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "level": issue.get("level"),
        "status": issue.get("status"),
    }


def build_sentry_issue_digest(
    issues: list[dict[str, Any]],
    *,
    stats_period: str,
    query: str,
) -> dict[str, Any]:
    """Build a bounded digest from the full issue page for model-facing summaries."""
    issue_count = len(issues)
    cluster_counts: dict[str, int] = {}
    enriched: list[tuple[tuple[int, int], dict[str, Any]]] = []

    for issue in issues:
        cluster = cluster_name_for_issue(issue)
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        classification = classify_issue(issue, cluster)
        enriched.append(
            (
                _impact_score(issue),
                slim_issue(issue, cluster=cluster, classification=classification),
            )
        )

    clusters = [
        {
            "name": name,
            "issue_count": count,
            "percent": round((count / issue_count) * 100) if issue_count else 0,
        }
        for name, count in sorted(cluster_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    top_issues = [
        issue
        for _, issue in sorted(enriched, key=lambda item: item[0], reverse=True)[
            :_TOP_ISSUE_LIMIT
        ]
    ]
    priority_issue = top_issues[0] if top_issues else None

    return {
        "issue_count": issue_count,
        "stats_period": stats_period,
        "query": query,
        "clusters": clusters,
        "top_issues": top_issues,
        "priority_issue_id": priority_issue.get("id") if priority_issue else None,
        "priority_short_id": priority_issue.get("short_id") if priority_issue else None,
    }
