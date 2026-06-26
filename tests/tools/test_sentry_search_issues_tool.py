"""Tests for SentrySearchIssuesTool (function-based, @tool decorated)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from integrations.sentry import _MAX_SENTRY_QUERY_LEN, _sanitize_sentry_query
from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.sentry_search_issues_tool import search_sentry_issues


class TestSentrySearchIssuesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_sentry_issues.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = search_sentry_issues.__opensre_registered_tool__
    assert rt.is_available({"sentry": {"connection_verified": True}}) is True
    assert rt.is_available({"sentry": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = search_sentry_issues.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["organization_slug"] == "my-org"
    assert params["sentry_token"] == "sntryu_test"


def test_extract_params_maps_resolved_config_dump_shape() -> None:
    """Regression: the resolved ``sentry`` source dict is a ``SentryConfig`` dump,
    so its keys are ``auth_token`` / ``base_url`` — NOT ``sentry_token`` /
    ``sentry_url``. Hard-indexing ``sentry['sentry_token']`` raised a KeyError
    that aborted every Sentry query in the gather/investigation loop."""
    from integrations.sentry import SentryConfig
    from tools.sentry_search_issues_tool import _search_issues_extract_params

    sources = {
        "sentry": SentryConfig(
            base_url="https://sentry.example.com",
            organization_slug="acme",
            auth_token="sntryu_resolved",
        ).model_dump()
        | {"connection_verified": True}
    }

    params = _search_issues_extract_params(sources)

    assert params["organization_slug"] == "acme"
    assert params["sentry_token"] == "sntryu_resolved"
    assert params["sentry_url"] == "https://sentry.example.com"


def test_run_returns_unavailable_when_no_config() -> None:
    # Stub env resolution so the test is hermetic: without this, a local .env
    # carrying real SENTRY_* creds makes _resolve_config fall back to them and
    # the tool reports available=True.
    with patch("tools.sentry_search_issues_tool.sentry_config_from_env", return_value=None):
        result = search_sentry_issues(organization_slug="", sentry_token="")
    assert result["available"] is False
    assert result["issues"] == []


def test_run_happy_path() -> None:
    fake_issues = [{"id": "1", "title": "TypeError", "status": "unresolved"}]
    with (
        patch("tools.sentry_search_issues_tool.list_sentry_issues", return_value=fake_issues),
        patch("tools.sentry_search_issues_tool.sentry_config_from_env", return_value=None),
    ):
        result = search_sentry_issues(
            organization_slug="my-org",
            sentry_token="tok_test",
            query="TypeError",
        )
    assert result["available"] is True
    assert len(result["issues"]) == 1
    assert result["query"] == "TypeError"


def test_run_empty_issues() -> None:
    with (
        patch("tools.sentry_search_issues_tool.list_sentry_issues", return_value=[]),
        patch("tools.sentry_search_issues_tool.sentry_config_from_env", return_value=None),
    ):
        result = search_sentry_issues(organization_slug="my-org", sentry_token="tok_test")
    assert result["available"] is True
    assert result["issues"] == []


@pytest.mark.integration
def test_live_env_config_searches_sentry_windows_issues() -> None:
    if not os.getenv("SENTRY_ORG_SLUG") or not os.getenv("SENTRY_AUTH_TOKEN"):
        pytest.skip("SENTRY_ORG_SLUG and SENTRY_AUTH_TOKEN are required for live Sentry search")

    result = search_sentry_issues(
        organization_slug="",
        sentry_token="",
        query="windows",
        limit=5,
    )

    assert result["available"] is True
    assert result["source"] == "sentry"
    assert result["query"] == "windows"
    assert isinstance(result["issues"], list)


# --- _sanitize_sentry_query tests ---


def test_sanitize_sentry_query_plain_term() -> None:
    assert _sanitize_sentry_query("TypeError") == "TypeError"


def test_sanitize_sentry_query_multiline_takes_first_line() -> None:
    raw = "TypeError: Cannot read\n  at foo (bar.ts:1)\n  at baz (qux.ts:2)"
    result = _sanitize_sentry_query(raw)
    assert "\n" not in result
    assert result == "TypeError: Cannot read"


def test_sanitize_sentry_query_truncates_long_query() -> None:
    long = "a" * (_MAX_SENTRY_QUERY_LEN + 50)
    result = _sanitize_sentry_query(long)
    assert len(result) == _MAX_SENTRY_QUERY_LEN


def test_sanitize_sentry_query_strips_whitespace() -> None:
    assert _sanitize_sentry_query("  hello world  ") == "hello world"


def test_sanitize_sentry_query_empty_string() -> None:
    assert _sanitize_sentry_query("") == ""


def test_build_issue_list_params_sanitizes_multiline_query() -> None:
    """_build_issue_list_params must collapse multi-line stack traces so the
    Sentry API does not return a 400 Bad Request."""
    from integrations.sentry import SentryConfig, _build_issue_list_params

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    multiline_query = "TypeError: Cannot read props\n  at src/foo.ts:10"
    params = dict(_build_issue_list_params(config, limit=10, query=multiline_query))
    assert "\n" not in str(params["query"])
    assert params["query"] == "TypeError: Cannot read props"


# --- limit / statsPeriod resolution tests (under-retrieval fix) ---


def test_clamp_issue_limit_caps_at_sentry_page_size() -> None:
    from integrations.sentry import _MAX_SENTRY_PAGE_SIZE, _clamp_issue_limit

    assert _clamp_issue_limit(1000) == _MAX_SENTRY_PAGE_SIZE
    assert _clamp_issue_limit(_MAX_SENTRY_PAGE_SIZE) == _MAX_SENTRY_PAGE_SIZE


def test_clamp_issue_limit_floors_at_one() -> None:
    from integrations.sentry import _clamp_issue_limit

    assert _clamp_issue_limit(0) == 1
    assert _clamp_issue_limit(-5) == 1


def test_clamp_issue_limit_defaults_on_bad_input() -> None:
    from integrations.sentry import DEFAULT_SENTRY_ISSUE_LIMIT, _clamp_issue_limit

    assert _clamp_issue_limit(None) == DEFAULT_SENTRY_ISSUE_LIMIT
    assert _clamp_issue_limit("not-an-int") == DEFAULT_SENTRY_ISSUE_LIMIT  # type: ignore[arg-type]


def test_default_issue_limit_is_full_sentry_page() -> None:
    """Regression: a tiny default limit was why searches "only found one issue"."""
    from integrations.sentry import _MAX_SENTRY_PAGE_SIZE, DEFAULT_SENTRY_ISSUE_LIMIT

    assert DEFAULT_SENTRY_ISSUE_LIMIT == _MAX_SENTRY_PAGE_SIZE


def test_build_issue_list_params_clamps_limit_into_page_range() -> None:
    from integrations.sentry import (
        _MAX_SENTRY_PAGE_SIZE,
        SentryConfig,
        _build_issue_list_params,
    )

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=5000, query=""))
    assert params["limit"] == str(_MAX_SENTRY_PAGE_SIZE)


def test_build_issue_list_params_uses_default_stats_period() -> None:
    from integrations.sentry import (
        DEFAULT_SENTRY_STATS_PERIOD,
        SentryConfig,
        _build_issue_list_params,
    )

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=10, query=""))
    assert params["statsPeriod"] == DEFAULT_SENTRY_STATS_PERIOD


def test_build_issue_list_params_explicit_stats_period_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.sentry import SentryConfig, _build_issue_list_params

    monkeypatch.setenv("SENTRY_STATS_PERIOD", "7d")
    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=10, query="", stats_period="14d"))
    assert params["statsPeriod"] == "14d"


def test_build_issue_list_params_stats_period_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.sentry import SentryConfig, _build_issue_list_params

    monkeypatch.setenv("SENTRY_STATS_PERIOD", "30d")
    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=10, query=""))
    assert params["statsPeriod"] == "30d"


def test_validate_sentry_config_reports_recent_issue_count() -> None:
    """Verify reports a meaningful recent issue count over the 7-day window."""
    from integrations.sentry import SentryConfig, validate_sentry_config

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    captured: dict[str, object] = {}

    def _fake_list(**kwargs: object) -> list[dict]:
        captured.update(kwargs)
        return [{"id": str(i)} for i in range(30)]

    with patch("integrations.sentry.list_sentry_issues", side_effect=_fake_list):
        result = validate_sentry_config(config)

    assert result.ok is True
    assert result.issue_count == 30
    assert "30 issue(s) in the last 7 days" in result.detail
    assert captured["stats_period"] == "7d"


def test_validate_sentry_config_saturated_count_uses_plus() -> None:
    """When the page saturates, the count is shown as ``N+`` (honest about
    the page-size cap rather than implying an exact total)."""
    from integrations.sentry import (
        _MAX_SENTRY_PAGE_SIZE,
        SentryConfig,
        validate_sentry_config,
    )

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    with patch(
        "integrations.sentry.list_sentry_issues",
        return_value=[{"id": str(i)} for i in range(_MAX_SENTRY_PAGE_SIZE)],
    ):
        result = validate_sentry_config(config)

    assert f"{_MAX_SENTRY_PAGE_SIZE}+ issue(s)" in result.detail


def test_search_tool_default_limit_is_full_page() -> None:
    from integrations.sentry import DEFAULT_SENTRY_ISSUE_LIMIT
    from tools.sentry_search_issues_tool import _search_issues_extract_params

    sources = {
        "sentry": {
            "organization_slug": "my-org",
            "sentry_token": "tok",
        }
    }
    params = _search_issues_extract_params(sources)
    assert params["limit"] == DEFAULT_SENTRY_ISSUE_LIMIT


def test_search_tool_forwards_limit_and_period_to_client() -> None:
    captured: dict[str, object] = {}

    def _fake_list(**kwargs: object) -> list[dict]:
        captured.update(kwargs)
        return []

    with (
        patch("tools.sentry_search_issues_tool.list_sentry_issues", side_effect=_fake_list),
        patch("tools.sentry_search_issues_tool.sentry_config_from_env", return_value=None),
    ):
        search_sentry_issues(
            organization_slug="my-org",
            sentry_token="tok",
            limit=50,
            stats_period="14d",
        )

    assert captured["limit"] == 50
    assert captured["stats_period"] == "14d"
