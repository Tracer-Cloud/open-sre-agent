"""Compact Sentry issue digests for model context budgets."""

from __future__ import annotations

import re
from typing import Any

_TOP_ISSUE_LIMIT = 5
_PRIORITY_CANDIDATE_LIMIT = 5
_TITLE_THEME_RE = re.compile(r"^\[([^\]]+)\]")
_CULPRIT_KEY_RE = re.compile(r"[^a-z0-9._-]+")

# Human-readable labels for common structural keys (LLM may rephrase further).
_STRUCTURAL_LABELS: dict[str, str] = {
    "integrations.datadog": "Datadog integration errors",
    "integrations.eks": "EKS / Kubernetes errors",
    "integrations.cloudtrail": "CloudTrail / AWS errors",
    "integrations.sentry": "Sentry integration errors",
    "integrations.github": "GitHub integration errors",
    "integrations.grafana": "Grafana integration errors",
    "tools.investigation": "Investigation pipeline errors",
    "core.llm": "LLM runtime / provider errors",
    "core.agent": "Agent runtime errors",
    "surfaces.cli": "CLI surface errors",
    "surfaces.interactive_shell": "Interactive shell errors",
    "platform.harness_ports": "Harness / integration wiring errors",
    "uncategorised": "Uncategorised errors",
}

_OPERATIONAL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("credentials", "blocks cloud/AWS credential-dependent workflows"),
    ("credit exhausted", "LLM billing or quota exhaustion"),
    ("stream failed", "investigation pipeline stream failure"),
    ("stopping pipeline", "investigation pipeline stopped"),
    ("unable to locate credentials", "missing cloud credentials"),
)


def _culprit_module(culprit: str) -> str:
    text = culprit.strip()
    if " in " in text:
        return text.split(" in ", 1)[0].strip()
    return text


def _sanitize_key(text: str) -> str:
    cleaned = _CULPRIT_KEY_RE.sub("_", text.lower()).strip("._")
    return cleaned or "unknown"


def _package_cluster_key(module: str, *, depth: int) -> str:
    parts = [part for part in module.split(".") if part]
    if not parts:
        return "uncategorised"
    return ".".join(parts[:depth])


def _title_theme_key(issue: dict[str, Any]) -> str | None:
    title = str(issue.get("title") or "").strip()
    match = _TITLE_THEME_RE.match(title)
    if not match:
        return None
    theme = _sanitize_key(match.group(1))
    return f"title-theme:{theme}" if theme != "unknown" else None


def structural_cluster_key_for_issue(issue: dict[str, Any]) -> str:
    """Assign a stable structural bucket from culprit, title theme, or issue id."""
    module = _culprit_module(str(issue.get("culprit") or ""))

    if module.startswith("integrations."):
        return _package_cluster_key(module, depth=3 if module.count(".") >= 2 else 2)

    if module.startswith("tools."):
        return _package_cluster_key(module, depth=3 if module.count(".") >= 2 else 2)

    for prefix in ("core.", "surfaces.", "platform.", "gateway."):
        if module.startswith(prefix):
            return _package_cluster_key(module, depth=2)

    if module and "." in module:
        return f"culprit:{_sanitize_key(module)}"

    title_theme = _title_theme_key(issue)
    if title_theme is not None:
        return title_theme

    short_id = str(issue.get("shortId") or "")
    if "-" in short_id:
        return f"issue-group:{short_id.rsplit('-', 1)[0].lower()}"

    project = issue.get("project")
    if isinstance(project, dict):
        slug = str(project.get("slug") or "").strip()
        if slug:
            return f"project:{slug}"
    elif isinstance(project, str) and project.strip():
        return f"project:{project.strip()}"

    if module:
        return f"culprit:{_sanitize_key(module)}"

    return "uncategorised"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def structural_cluster_label(key: str, *, sample_titles: tuple[str, ...] = ()) -> str:
    if key in _STRUCTURAL_LABELS:
        base = _STRUCTURAL_LABELS[key]
    elif key.startswith("integrations."):
        vendor = key.removeprefix("integrations.").split(".", 1)[0]
        base = f"{vendor.replace('_', ' ').title()} integration errors"
    elif key.startswith("tools."):
        package = key.removeprefix("tools.").split(".", 1)[0]
        base = f"{package.replace('_', ' ').title()} tool errors"
    elif key.startswith("title-theme:"):
        theme = key.removeprefix("title-theme:").replace("_", " ")
        base = f"{theme.title()} errors (from issue titles)"
    elif key.startswith("culprit:"):
        base = f"Code path {key.removeprefix('culprit:').replace('_', '.')}"
    elif key.startswith("project:"):
        slug = key.removeprefix("project:")
        base = f"Sentry project {slug} (fallback bucket — inspect samples)"
    elif key.startswith("issue-group:"):
        base = f"Issue family {key.removeprefix('issue-group:').upper()}"
    else:
        base = key

    if sample_titles:
        return f"{base} — e.g. {_truncate(sample_titles[0], 72)}"
    return base


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

    for keyword, reason in _OPERATIONAL_KEYWORDS:
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


def slim_issue(
    issue: dict[str, Any],
    *,
    structural_cluster: str,
    classification: str,
    impact_score: int,
    impact_reasons: list[str],
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
        "business_impact_score": impact_score,
        "impact_reasons": impact_reasons,
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
    cluster_titles: dict[str, list[str]] = {}
    enriched: list[tuple[int, dict[str, Any]]] = []

    for issue in issues:
        structural_cluster = structural_cluster_key_for_issue(issue)
        cluster_counts[structural_cluster] = cluster_counts.get(structural_cluster, 0) + 1
        title = str(issue.get("title") or "").strip()
        if title:
            cluster_titles.setdefault(structural_cluster, []).append(title)
        classification = classify_issue(issue)
        impact_score, impact_reasons = business_impact_score(issue)
        enriched.append(
            (
                impact_score,
                slim_issue(
                    issue,
                    structural_cluster=structural_cluster,
                    classification=classification,
                    impact_score=impact_score,
                    impact_reasons=impact_reasons,
                ),
            )
        )

    structural_clusters = []
    for key, count in sorted(cluster_counts.items(), key=lambda item: item[1], reverse=True):
        titles = tuple(cluster_titles.get(key, ()))
        top_titles = tuple(
            title for title, _ in sorted(
                ((title, titles.count(title)) for title in set(titles)),
                key=lambda item: item[1],
                reverse=True,
            )[:2]
        )
        structural_clusters.append(
            {
                "key": key,
                "label": structural_cluster_label(key, sample_titles=top_titles),
                "issue_count": count,
                "percent": round((count / issue_count) * 100) if issue_count else 0,
                "sample_titles": list(top_titles),
            }
        )

    ranked_issues = [
        issue for _, issue in sorted(enriched, key=lambda item: item[0], reverse=True)
    ]
    top_issues = ranked_issues[:_TOP_ISSUE_LIMIT]
    priority_candidates = [
        {
            "short_id": issue["short_id"],
            "title": issue.get("title"),
            "structural_cluster": issue.get("structural_cluster"),
            "business_impact_score": issue.get("business_impact_score"),
            "impact_reasons": issue.get("impact_reasons"),
            "count": issue.get("count"),
            "user_count": issue.get("user_count"),
        }
        for issue in ranked_issues[:_PRIORITY_CANDIDATE_LIMIT]
    ]
    priority_issue = ranked_issues[0] if ranked_issues else None

    return {
        "issue_count": issue_count,
        "stats_period": stats_period,
        "query": query,
        "structural_clusters": structural_clusters,
        "top_issues": top_issues,
        "priority_candidates": priority_candidates,
        "priority_issue_id": priority_issue.get("id") if priority_issue else None,
        "priority_short_id": priority_issue.get("short_id") if priority_issue else None,
        "priority_impact_reasons": (
            priority_issue.get("impact_reasons") if priority_issue else []
        ),
    }


# Backward-compatible alias used by older tests/callers.
cluster_name_for_issue = structural_cluster_key_for_issue
