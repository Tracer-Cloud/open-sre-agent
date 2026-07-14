"""Tests for Sentry issue digest assembly."""

from __future__ import annotations

from integrations.sentry.issue_digest import (
    build_sentry_issue_digest,
    scope_summary_for_digest,
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
            "title": "[cloudtrail] lookup_events failed region=us-east-1",
            "culprit": "",
            "project": {"slug": "python"},
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
    assert digest["page_complete"] is True
    assert digest["page_saturated"] is False
    assert digest["completeness"] == "complete_page"
    assert digest["scope_summary"] == scope_summary_for_digest(
        issue_count=2,
        stats_period="7d",
        query="is:unresolved",
    )
    assert "complete" in digest["scope_note"]
    assert digest["stats_period_label"] == "last 7 days"
    assert digest["structural_clusters"][0]["key"] == "integrations.datadog.client"
    assert digest["structural_clusters"][0]["sample_titles"]
    assert digest["structural_clusters"][0]["sample_short_ids"] == ["PYTHON-ET"]
    assert digest["structural_clusters"][0]["percent"] == 50
    assert "e.g." in digest["structural_clusters"][0]["label"]
    assert digest["priority_candidates"][0]["short_id"] == "PYTHON-Y8"
    assert digest["top_issues"][0]["short_id"] == "PYTHON-Y8"
    assert digest["priority_short_id"] == "PYTHON-Y8"
    assert digest["priority_impact_reasons"]


def test_scope_metadata_marks_saturated_page() -> None:
    digest = build_sentry_issue_digest(
        [{"id": str(i), "shortId": f"PYTHON-{i}"} for i in range(100)],
        stats_period="7d",
        query="is:unresolved",
        page_limit=100,
    )

    assert digest["issue_count"] == 100
    assert digest["page_saturated"] is True
    assert digest["page_complete"] is False
    assert digest["completeness"] == "partial_page"
    assert digest["issue_count_label"] == "100+"
    assert "first page" in digest["scope_summary"]
    assert "additional unresolved groups" in digest["scope_note"]
    assert digest["percent_basis"] == "returned_page"


def test_scope_metadata_empty_result_discourages_silent_widen() -> None:
    digest = build_sentry_issue_digest([], stats_period="24h", query="is:unresolved")

    assert digest["has_results"] is False
    assert digest["completeness"] == "empty"
    assert "last 24 hours" in digest["scope_summary"]
    assert "Do not widen" in digest["scope_note"]
