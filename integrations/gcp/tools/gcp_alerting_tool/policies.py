"""Alert-policy and condition normalization.

``notificationChannels`` is reported as a **count** and never as resource
names. Channel configuration holds responder email addresses, phone numbers and
webhook URLs; the structural way not to leak them is not to fetch or forward
the resource at all, which is the same reasoning the Rootly On-Call PII
allowlist follows.
"""

from __future__ import annotations

from typing import Any

#: ``Condition`` oneof field → the ``kind`` reported to the model. A condition
#: carries exactly one of these; anything else is a shape Google added later.
_CONDITION_KINDS: tuple[tuple[str, str], ...] = (
    ("conditionThreshold", "threshold"),
    ("conditionAbsent", "absence"),
    ("conditionMatchedLog", "log_match"),
    ("conditionMonitoringQueryLanguage", "mql"),
    ("conditionPrometheusQueryLanguage", "promql"),
    ("conditionSql", "sql"),
)

#: Kinds whose payload is a Cloud Monitoring filter, i.e. paste-ready into
#: gcp_monitoring_query. The rest carry a raw query string instead.
_FILTER_KINDS = frozenset({"threshold", "absence", "log_match"})


def _trailing(resource_name: str) -> str:
    """Return the last ``/``-separated segment of a resource name."""
    if not resource_name:
        return ""
    return resource_name.rstrip("/").split("/")[-1]


def normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``Condition``, whichever of the six shapes it is.

    Exactly one of ``filter`` and ``query`` is non-empty: a threshold, absence
    or log-match condition is a filter; MQL, PromQL and SQL are queries.
    """
    kind = "unknown"
    body: dict[str, Any] = {}
    for field, name in _CONDITION_KINDS:
        candidate = condition.get(field)
        if isinstance(candidate, dict):
            kind = name
            body = candidate
            break

    payload = str(body.get("filter", "") or "") or str(body.get("query", "") or "")
    aggregation = body.get("aggregations") or []
    first = aggregation[0] if aggregation and isinstance(aggregation[0], dict) else {}

    return {
        "display_name": str(condition.get("displayName", "") or ""),
        "kind": kind,
        "filter": payload if kind in _FILTER_KINDS else "",
        "query": "" if kind in _FILTER_KINDS else payload,
        "comparison": str(body.get("comparison", "") or ""),
        "threshold": body.get("thresholdValue", 0.0),
        "duration": str(body.get("duration", "") or ""),
        "aligner": str(first.get("perSeriesAligner", "") or ""),
        "reducer": str(first.get("crossSeriesReducer", "") or ""),
        "group_by": first.get("groupByFields") or [],
    }


def normalize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``AlertPolicy``.

    ``invalid_reason`` is worth as much as the conditions: Google marking a
    policy invalid is a live and common reason an alert "never fired", and it
    is invisible from anywhere else.
    """
    conditions = [
        normalize_condition(condition)
        for condition in policy.get("conditions") or []
        if isinstance(condition, dict)
    ]
    validity = policy.get("validity") or {}
    documentation = policy.get("documentation") or {}
    mutation = policy.get("mutationRecord") or {}
    strategy = policy.get("alertStrategy") or {}

    return {
        "id": _trailing(str(policy.get("name", "") or "")),
        "display_name": str(policy.get("displayName", "") or ""),
        # Absent means enabled: the API omits the field on a default policy.
        "enabled": bool(policy.get("enabled", True)),
        "severity": str(policy.get("severity", "") or ""),
        "combiner": str(policy.get("combiner", "") or ""),
        "condition_count": len(conditions),
        "conditions": conditions,
        # The shaper emits a fixed set of keys and no personal identifier — no
        # email address, no phone number, no notification-channel resource name.
        # Follows the structural-allowlist precedent in integrations/rootly/.
        "notification_channel_count": len(policy.get("notificationChannels") or []),
        "auto_close": str(strategy.get("autoClose", "") or ""),
        "documentation_subject": str(documentation.get("subject", "") or ""),
        "user_labels": policy.get("userLabels") or {},
        "invalid_reason": str(validity.get("message", "") or ""),
        "last_modified_at": str(mutation.get("mutateTime", "") or ""),
    }


def keep_for_name(policy: dict[str, Any], needle: str) -> bool:
    """Case-insensitive substring match against the policy display name."""
    if not needle:
        return True
    return needle.casefold() in str(policy.get("display_name", "") or "").casefold()


def condition_filters(policies: list[dict[str, Any]]) -> list[str]:
    """Deduplicated non-empty condition filters, first-seen order.

    This is the paste-into-gcp_monitoring_query path: the agent re-runs the
    alert's own query rather than inventing an approximation of it.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for policy in policies:
        for condition in policy.get("conditions") or []:
            expression = str(condition.get("filter", "") or "")
            if expression and expression not in seen:
                seen.add(expression)
                ordered.append(expression)
    return ordered
