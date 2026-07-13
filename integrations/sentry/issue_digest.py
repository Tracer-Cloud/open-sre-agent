"""Compact Sentry issue digests for model context budgets."""

from __future__ import annotations

from typing import Any

_TOP_ISSUE_LIMIT = 5

# Human-readable labels for common structural keys (LLM may rephrase further).
_STRUCTURAL_LABELS: dict[str, str] = {
    "integrations.datadog": "Datadog integration",
    "integrations.eks": "EKS / Kubernetes",
    "integrations.cloudtrail": "CloudTrail / AWS",
    "integrations.sentry": "Sentry integration",
    "integrations.github": "GitHub integration",
    "integrations.grafana": "Grafana integration",
    "tools.investigation": "Investigation pipeline",
    "core.llm": "LLM runtime",
    "core.agent": "Agent runtime",
    "surfaces.cli": "CLI surface",
    "surfaces.interactive_shell": "Interactive shell",
    "platform.harness_ports": "Harness / integrations",
    "uncategorised": "Uncategorised",
}


def _culprit_module(culprit: str) -> str:
    text = culprit.strip()
    if " in " in text:
        return text.split(" in ", 1)[0].strip()
    return text


def structural_cluster_key_for_issue(issue: dict[str, Any]) -> str:
    """Assign a stable structural bucket from culprit, project, or issue id."""
    module = _culprit_module(str(issue.get("culprit") or ""))

    if module.startswith("integrations."):
        parts = module.split(".")
        return f"integrations.{parts[1]}" if len(parts) >= 2 else "integrations"

    if module.startswith("tools."):
        parts = module.split(".")
        return f"tools.{parts[1]}" if len(parts) >= 2 else "tools"

    for prefix in ("core.", "surfaces.", "platform.", "gateway."):
        if module.startswith(prefix):
            parts = module.split(".")
            return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else parts[0]

    project = issue.get("project")
    if isinstance(project, dict):
        slug = str(project.get("slug") or "").strip()
        if slug:
            return f"project:{slug}"
    elif isinstance(project, str) and project.strip():
        return f"project:{project.strip()}"

    short_id = str(issue.get("shortId") or "")
    if "-" in short_id:
        return f"issue-group:{short_id.rsplit('-', 1)[0].lower()}"

    if module:
        return module.split(".")[0] if "." in module else module

    return "uncategorised"


def structural_cluster_label(key: str) -> str:
    if key in _STRUCTURAL_LABELS:
        return _STRUCTURAL_LABELS[key]
    if key.startswith("project:"):
        return f"Project {key.removeprefix('project:')}"
    if key.startswith("issue-group:"):
        return f"Issue group {key.removeprefix('issue-group:').upper()}"
    return key


def classify_issue(issue: dict[str, Any]) -> str:
    if issue.get("regressedAt"):
        return "regression"
    if issue.get("status") == "new":
        return "new failure"
    return "ongoing"


def _impact_score(issue: dict[str, Any]) -> tuple[int, int]:
    user_count = int(issue.get("userCount") or 0)
    event_count = int(issue.get("count") or 0)
    return (user_count, event_count)


def slim_issue(
    issue: dict[str, Any],
    *,
    structural_cluster: str,
    classification: str,
) -> dict[str, Any]:
    return {
        "id": issue.get("id"),
        "short_id": issue.get("shortId") or issue.get("id"),
        "title": issue.get("title"),
        "culprit": issue.get("culprit"),
        "structural_cluster": structural_cluster,
        "structural_label": structural_cluster_label(structural_cluster),
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
        structural_cluster = structural_cluster_key_for_issue(issue)
        cluster_counts[structural_cluster] = cluster_counts.get(structural_cluster, 0) + 1
        classification = classify_issue(issue)
        enriched.append(
            (
                _impact_score(issue),
                slim_issue(
                    issue,
                    structural_cluster=structural_cluster,
                    classification=classification,
                ),
            )
        )

    structural_clusters = [
        {
            "key": key,
            "label": structural_cluster_label(key),
            "issue_count": count,
            "percent": round((count / issue_count) * 100) if issue_count else 0,
        }
        for key, count in sorted(cluster_counts.items(), key=lambda item: item[1], reverse=True)
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
        "structural_clusters": structural_clusters,
        "top_issues": top_issues,
        "priority_issue_id": priority_issue.get("id") if priority_issue else None,
        "priority_short_id": priority_issue.get("short_id") if priority_issue else None,
    }


# Backward-compatible alias used by older tests/callers.
cluster_name_for_issue = structural_cluster_key_for_issue
