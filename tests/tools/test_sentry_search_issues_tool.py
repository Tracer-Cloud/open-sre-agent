"""Tests for SentrySearchIssuesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.integrations.sentry import _MAX_SENTRY_QUERY_LEN, _sanitize_sentry_query
from app.tools.SentrySearchIssuesTool import search_sentry_issues
from tests.tools.conftest import BaseToolContract, mock_agent_state


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


def test_run_returns_unavailable_when_no_config() -> None:
    result = search_sentry_issues(organization_slug="", sentry_token="")
    assert result["available"] is False
    assert result["issues"] == []


def test_run_happy_path() -> None:
    fake_issues = [{"id": "1", "title": "TypeError", "status": "unresolved"}]
    with (
        patch("app.tools.SentrySearchIssuesTool.list_sentry_issues", return_value=fake_issues),
        patch("app.tools.SentrySearchIssuesTool.sentry_config_from_env", return_value=None),
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
        patch("app.tools.SentrySearchIssuesTool.list_sentry_issues", return_value=[]),
        patch("app.tools.SentrySearchIssuesTool.sentry_config_from_env", return_value=None),
    ):
        result = search_sentry_issues(organization_slug="my-org", sentry_token="tok_test")
    assert result["available"] is True
    assert result["issues"] == []


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
    from app.integrations.sentry import SentryConfig, _build_issue_list_params

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    multiline_query = "TypeError: Cannot read props\n  at src/foo.ts:10"
    params = dict(_build_issue_list_params(config, limit=10, query=multiline_query))
    assert "\n" not in str(params["query"])
    assert params["query"] == "TypeError: Cannot read props"
