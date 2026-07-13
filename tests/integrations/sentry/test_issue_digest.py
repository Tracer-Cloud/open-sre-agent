"""Tests for Sentry issue digest helpers."""

from __future__ import annotations

from integrations.sentry.issue_digest import (
    build_sentry_issue_digest,
    structural_cluster_key_for_issue,
    structural_cluster_label,
)


def test_structural_cluster_key_uses_integration_package() -> None:
    assert (
        structural_cluster_key_for_issue(
            {"culprit": "integrations.datadog.client in list_monitors"}
        )
        == "integrations.datadog"
    )
    assert (
        structural_cluster_key_for_issue(
            {"culprit": "integrations.eks.eks_k8s_client in build_k8s_clients"}
        )
        == "integrations.eks"
    )


def test_structural_cluster_key_uses_issue_group_prefix() -> None:
    assert (
        structural_cluster_key_for_issue({"shortId": "TRACER-CLIENT-4C", "culprit": ""})
        == "issue-group:tracer-client"
    )


def test_build_sentry_issue_digest_structural_clusters_and_ranks() -> None:
    issues = [
        {
            "id": "1",
            "shortId": "PYTHON-ET",
            "title": "HTTPStatusError 403 Forbidden",
            "culprit": "integrations.datadog.client in list_monitors",
            "count": 10,
            "userCount": 0,
            "firstSeen": "2026-07-10T00:00:00Z",
            "lastSeen": "2026-07-13T00:00:00Z",
            "status": "unresolved",
            "level": "error",
        },
        {
            "id": "2",
            "shortId": "PYTHON-Y8",
            "title": "RuntimeError: No AWS credentials available",
            "culprit": "integrations.cloudtrail.lookup in lookup_events",
            "count": 4,
            "userCount": 2,
            "firstSeen": "2026-07-12T00:00:00Z",
            "lastSeen": "2026-07-13T00:00:00Z",
            "status": "new",
            "level": "error",
        },
    ]

    digest = build_sentry_issue_digest(issues, stats_period="7d", query="is:unresolved")

    assert digest["issue_count"] == 2
    assert digest["stats_period"] == "7d"
    assert digest["query"] == "is:unresolved"
    assert len(digest["structural_clusters"]) == 2
    assert digest["structural_clusters"][0]["key"] == "integrations.datadog"
    assert digest["structural_clusters"][0]["label"] == structural_cluster_label(
        "integrations.datadog"
    )
    assert digest["top_issues"][0]["short_id"] == "PYTHON-Y8"
    assert digest["top_issues"][0]["structural_cluster"] == "integrations.cloudtrail"
    assert digest["priority_issue_id"] == "2"
