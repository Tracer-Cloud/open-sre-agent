"""Tests for shared Sentry integration helpers."""

from __future__ import annotations

from typing import Any

import httpx

from app.integrations.sentry import SentryConfig, list_sentry_issues


def _config() -> SentryConfig:
    return SentryConfig(organization_slug="ide-zy", auth_token="tok_test")


def _capture_sentry_issue_params(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: list[tuple[str, object]],
        timeout: float,
    ) -> httpx.Response:
        captured["params"] = params
        return httpx.Response(200, json=[], request=httpx.Request(method, url))

    monkeypatch.setattr("app.integrations.sentry.httpx.request", fake_request)
    return captured


def test_list_sentry_issues_quotes_exception_signature_queries(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "TypeError: Cannot read properties of null (reading mailapi) at src/mail/sync.ts:142"
    assert list_sentry_issues(config=_config(), query=query, limit=10) == []

    assert ("query", f'"{query}"') in captured["params"]


def test_list_sentry_issues_preserves_structured_queries(monkeypatch: Any) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "is:unresolved level:error error.type:TypeError url:http://example.test/login"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", query) in captured["params"]


def test_list_sentry_issues_escapes_quotes_in_exception_signatures(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = 'TypeError: Cannot read "mailapi" at src/mail/sync.ts:142'
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = '"TypeError: Cannot read \\"mailapi\\" at src/mail/sync.ts:142"'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_quotes_os_errors_with_word_colons(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "OSError: No such file or directory: '/tmp/cache/index.json'"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", f'"{query}"') in captured["params"]


def test_list_sentry_issues_quotes_lowercase_panic_signatures(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "panic: runtime error: invalid memory address or nil pointer dereference"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", f'"{query}"') in captured["params"]


def test_list_sentry_issues_quotes_bare_colon_exception_signatures(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "KeyError:missing_key"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", f'"{query}"') in captured["params"]
