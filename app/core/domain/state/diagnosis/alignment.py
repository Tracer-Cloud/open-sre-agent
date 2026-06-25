"""Heuristic check that root_cause text aligns with root_cause_category."""

from __future__ import annotations

from app.core.domain.types.root_cause_categories import (
    GROUP_DATABASE,
    GROUP_KUBERNETES,
    GROUP_NETWORK,
    categories_by_group,
)

_CATEGORY_GROUPS: dict[str, str] = {
    entry.name: group for group, entries in categories_by_group().items() for entry in entries
}

# Strong keyword signals per taxonomy group. Require multiple hits before flagging.
_GROUP_SIGNALS: dict[str, tuple[str, ...]] = {
    GROUP_DATABASE: (
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "redis",
        "connection pool",
        "max_connections",
        "replication lag",
        "slow query",
        "sql database",
    ),
    GROUP_KUBERNETES: (
        "oomkilled",
        "oom killed",
        "crashloop",
        "crash loop",
        "kubelet",
        "liveness probe",
        "readiness probe",
        "imagepull",
        "evicted",
    ),
    GROUP_NETWORK: (
        "dns resolution",
        "dns failure",
        "tls certificate",
        "security group",
        "nat gateway",
        "network partition",
    ),
}

_SKIP_CATEGORIES = frozenset({"healthy", "unknown"})
_MIN_GROUP_SIGNAL_HITS = 2
_VALIDITY_PENALTY = 0.15


def detect_category_text_mismatch(root_cause: str, root_cause_category: str) -> str | None:
    """Return a mismatch reason when text strongly signals a different taxonomy group."""
    category = root_cause_category.strip()
    if category in _SKIP_CATEGORIES:
        return None

    category_group = _CATEGORY_GROUPS.get(category)
    if category_group is None:
        return None

    text = root_cause.lower()
    # Only compare groups we have keyword signals for — avoids false positives when
    # the category is e.g. code_and_configuration but the text describes downstream
    # database symptoms caused by a deploy.
    if category_group not in _GROUP_SIGNALS:
        return None

    group_scores = {
        group: sum(1 for keyword in keywords if keyword in text)
        for group, keywords in _GROUP_SIGNALS.items()
    }
    best_group = max(group_scores, key=lambda group: group_scores[group])
    best_score = group_scores[best_group]
    if best_score < _MIN_GROUP_SIGNAL_HITS or best_group == category_group:
        return None

    category_tokens = [token for token in category.replace("_", " ").split() if len(token) >= 3]
    if any(token in text for token in category_tokens):
        return None

    return (
        f"root cause text signals {best_group} ({best_score} keyword hits) "
        f"but category {category!r} is {category_group}"
    )


def apply_category_alignment_adjustments(
    *,
    root_cause: str,
    root_cause_category: str,
    validity_score: float,
    investigation_recommendations: list[str],
) -> tuple[float, list[str], bool, str | None]:
    """Lower confidence and add a recommendation when text and category disagree."""
    mismatch_reason = detect_category_text_mismatch(root_cause, root_cause_category)
    if mismatch_reason is None:
        return validity_score, investigation_recommendations, False, None

    adjusted_score = max(0.0, validity_score - _VALIDITY_PENALTY)
    recommendation = (
        "The root cause category may not match the written explanation — "
        "review the classification before acting on it."
    )
    if recommendation in investigation_recommendations:
        return adjusted_score, investigation_recommendations, True, mismatch_reason
    return (
        adjusted_score,
        [*investigation_recommendations, recommendation],
        True,
        mismatch_reason,
    )
