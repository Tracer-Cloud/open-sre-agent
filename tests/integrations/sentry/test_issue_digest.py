"""Tests for Sentry issue digest helpers."""

from __future__ import annotations

from integrations.sentry.issue_digest import (
    build_sentry_issue_digest,
    cluster_name_for_issue,
)


def test_cluster_name_for_issue_maps_auth_and_windows() -> None:
    assert (
        cluster_name_for_issue({"title": "APIKeyMissingError in auth/validate"})
        == "Auth / API key errors"
    )
    assert (
        cluster_name_for_issue({"title": "WindowsInstallFailed at setup.exe"})
        == "Windows / OS install failures"
    )


def test_build_sentry_issue_digest_clusters_and_ranks() -> None:
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
            "culprit": "integrations.eks.eks_k8s_client in build_k8s_clients",
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
    assert digest["clusters"]
    assert digest["top_issues"][0]["short_id"] == "PYTHON-Y8"
    assert digest["priority_issue_id"] == "2"
