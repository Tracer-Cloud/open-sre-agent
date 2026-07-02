"""Tests for parsing Sentry issue URLs."""

from __future__ import annotations

from integrations.sentry.issue_url import parse_sentry_issue_url


def test_subdomain_form() -> None:
    ref = parse_sentry_issue_url("https://myorg.sentry.io/issues/12345/")
    assert ref is not None
    assert ref.issue_id == "12345"
    assert ref.organization_slug == "myorg"


def test_subdomain_form_with_event_suffix() -> None:
    ref = parse_sentry_issue_url("https://myorg.sentry.io/issues/ABC123/events/latest/")
    assert ref is not None
    assert ref.issue_id == "ABC123"
    assert ref.organization_slug == "myorg"


def test_organizations_path_form() -> None:
    ref = parse_sentry_issue_url("https://sentry.io/organizations/acme/issues/999/")
    assert ref is not None
    assert ref.issue_id == "999"
    assert ref.organization_slug == "acme"


def test_self_hosted_form() -> None:
    ref = parse_sentry_issue_url("https://sentry.example.com/organizations/acme/issues/77/")
    assert ref is not None
    assert ref.issue_id == "77"
    assert ref.organization_slug == "acme"


def test_non_sentry_url_is_rejected() -> None:
    # Must not match a GitHub issue URL just because it has /issues/<n>.
    assert parse_sentry_issue_url("https://github.com/foo/bar/issues/123") is None


def test_garbage_inputs() -> None:
    assert parse_sentry_issue_url("") is None
    assert parse_sentry_issue_url(None) is None
    assert parse_sentry_issue_url("not a url") is None
    assert parse_sentry_issue_url("https://myorg.sentry.io/dashboard/") is None
