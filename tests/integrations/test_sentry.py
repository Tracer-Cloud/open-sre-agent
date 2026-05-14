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


def test_list_sentry_issues_preserves_quoted_structured_filter_values(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = 'is:unresolved title:"Cannot read properties" level:error'
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", query) in captured["params"]


def test_list_sentry_issues_preserves_negated_structured_queries(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "!is:resolved !level:info"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", query) in captured["params"]


def test_list_sentry_issues_preserves_plain_colon_text_queries(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "team: backend rollout"
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


def test_list_sentry_issues_quotes_package_qualified_exception_signatures(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "java.lang.NullPointerException: Cannot invoke getName()"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", f'"{query}"') in captured["params"]


def test_list_sentry_issues_quotes_bare_colon_exception_signatures(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "KeyError:missing_key"
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", f'"{query}"') in captured["params"]


def test_list_sentry_issues_preserves_filters_after_exception_text(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "TypeError: cannot read property 'x' is:unresolved level:error"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = "\"TypeError: cannot read property 'x'\" is:unresolved level:error"
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_preserves_filters_after_lowercase_panic(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "panic: nil pointer is:unresolved level:fatal"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = '"panic: nil pointer" is:unresolved level:fatal'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_preserves_prequoted_exception_text_with_filters(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = '"TypeError: cannot read property" is:unresolved'
    list_sentry_issues(config=_config(), query=query, limit=10)

    assert ("query", query) in captured["params"]


def test_list_sentry_issues_keeps_filter_tokens_inside_exception_messages(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "TypeError: unexpected level:error state is:unresolved"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = '"TypeError: unexpected level:error state" is:unresolved'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_drops_bare_exception_separator_before_filters(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "TypeError: is:unresolved level:error"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = '"TypeError" is:unresolved level:error'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_preserves_filters_before_exception_text(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "is:unresolved level:error TypeError: cannot read property"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = 'is:unresolved level:error "TypeError: cannot read property"'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_preserves_filters_around_exception_text(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "is:unresolved panic: nil pointer level:fatal"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = 'is:unresolved "panic: nil pointer" level:fatal'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_preserves_negated_filters_with_exception_text(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "TypeError: cannot read property !is:resolved !level:info"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = '"TypeError: cannot read property" !is:resolved !level:info'
    assert ("query", expected_query) in captured["params"]


def test_list_sentry_issues_preserves_leading_negated_filters(
    monkeypatch: Any,
) -> None:
    captured = _capture_sentry_issue_params(monkeypatch)

    query = "!is:resolved !level:info TypeError: cannot read property"
    list_sentry_issues(config=_config(), query=query, limit=10)

    expected_query = '!is:resolved !level:info "TypeError: cannot read property"'
    assert ("query", expected_query) in captured["params"]
