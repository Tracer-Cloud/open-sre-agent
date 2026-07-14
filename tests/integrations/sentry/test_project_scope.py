"""Tests for structured Sentry project scope helpers."""

from __future__ import annotations

from integrations.sentry.project_scope import (
    apply_sentry_project_scope,
    payload_project_slug,
)
from integrations.sentry.tools.sentry_search_issues_tool import _search_issues_extract_params


class TestPayloadProjectSlug:
    def test_prefers_project_slug(self) -> None:
        assert payload_project_slug({"project_slug": "checkout-api"}) == "checkout-api"

    def test_accepts_project_alias(self) -> None:
        assert payload_project_slug({"project": "api"}) == "api"


class TestApplySentryProjectScope:
    def test_sets_project_on_sentry_source(self) -> None:
        resolved = {
            "sentry": {
                "connection_verified": True,
                "organization_slug": "tracer",
                "auth_token": "token",
                "project_slug": "",
            }
        }
        scoped = apply_sentry_project_scope(resolved, "checkout-api")
        assert scoped["sentry"]["project_slug"] == "checkout-api"

    def test_noop_without_sentry(self) -> None:
        resolved = {"datadog": {"connection_verified": True}}
        assert apply_sentry_project_scope(resolved, "checkout-api") == resolved

    def test_extract_params_uses_scoped_project(self) -> None:
        resolved = apply_sentry_project_scope(
            {
                "sentry": {
                    "connection_verified": True,
                    "organization_slug": "tracer",
                    "auth_token": "token",
                    "base_url": "https://sentry.io",
                }
            },
            "checkout-api",
        )
        params = _search_issues_extract_params(resolved)
        assert params["project_slug"] == "checkout-api"
