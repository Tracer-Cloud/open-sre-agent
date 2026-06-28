"""Tests for the GitHub REST integration client."""

from __future__ import annotations

import json
from email.message import Message
from typing import Any
from urllib import error, request

import pytest

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token


class _Response:
    def __init__(
        self, payload: Any, *, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self._payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _RawResponse(_Response):
    def __init__(self, payload: str, *, headers: dict[str, str] | None = None) -> None:
        super().__init__({}, headers=headers)
        self._raw_payload = payload

    def read(self) -> bytes:
        return self._raw_payload.encode("utf-8")


def test_resolve_github_token_prefers_explicit_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert resolve_github_token("explicit") == "explicit"
    assert resolve_github_token(None) == "env-token"


def test_missing_token_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    client = GitHubRestClient(github_token=None)

    with pytest.raises(GitHubApiError) as exc:
        client.request("GET", "/repos/o/r/issues")

    assert exc.value.status_code is None
    assert "GitHub token is required" in str(exc.value)


def test_paginate_follows_link_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        url = req.full_url
        calls.append(url)
        if "page=2" in url:
            return _Response([{"number": 2}], headers={})
        return _Response(
            [{"number": 1}],
            headers={"Link": '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'},
        )

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    assert client.paginate("/repos/o/r/issues") == [{"number": 1}, {"number": 2}]
    assert len(calls) == 2


def test_http_error_preserves_status_and_rate_limit_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(_req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "123"
        raise error.HTTPError(
            url="https://api.github.com/repos/o/r/issues",
            code=403,
            msg="rate limited",
            hdrs=headers,
            fp=None,
        )

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    with pytest.raises(GitHubApiError) as exc:
        client.request("GET", "/repos/o/r/issues")

    assert exc.value.status_code == 403
    assert exc.value.rate_limit_remaining == "0"
    assert exc.value.rate_limit_reset == "123"


def test_invalid_json_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_req: request.Request, timeout: int = 0) -> _RawResponse:  # noqa: ARG001
        return _RawResponse("not-json")

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    with pytest.raises(GitHubApiError) as exc:
        client.request("GET", "/repos/o/r/issues")

    assert "invalid JSON" in str(exc.value)
