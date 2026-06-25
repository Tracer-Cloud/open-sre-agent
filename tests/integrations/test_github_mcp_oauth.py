"""Tests for GitHub MCP OAuth device-flow authorization."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.github_mcp_oauth as oauth


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _device_code(expires_in: int = 900, interval: int = 5) -> oauth.GitHubDeviceCode:
    return oauth.GitHubDeviceCode(
        device_code="dev-123",
        user_code="WXYZ-1234",
        verification_uri="https://github.com/login/device",
        expires_in=expires_in,
        interval=interval,
    )


# --- client id resolution ---


def test_resolve_client_id_prefers_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_OAUTH_CLIENT_ID", "env-id")
    assert oauth.resolve_github_oauth_client_id("explicit-id") == "explicit-id"


def test_resolve_client_id_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_OAUTH_CLIENT_ID", "env-id")
    assert oauth.resolve_github_oauth_client_id() == "env-id"


def test_resolve_client_id_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setattr(oauth, "DEFAULT_GITHUB_OAUTH_CLIENT_ID", "")
    assert oauth.resolve_github_oauth_client_id() == ""


# --- request_github_device_code ---


def test_request_device_code_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        return _FakeResponse(
            {
                "device_code": "dev-123",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 899,
                "interval": 7,
            }
        )

    monkeypatch.setattr(oauth.httpx, "post", _fake_post)

    code = oauth.request_github_device_code(client_id="cid", scopes=["repo", "read:org"])

    assert captured["url"] == oauth.GITHUB_DEVICE_CODE_URL
    assert captured["data"] == {"client_id": "cid", "scope": "repo read:org"}
    assert code.device_code == "dev-123"
    assert code.user_code == "WXYZ-1234"
    assert code.interval == 7
    assert code.expires_in == 899


def test_request_device_code_raises_on_disabled_device_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        oauth.httpx,
        "post",
        lambda *_a, **_k: _FakeResponse({"error": "device_flow_disabled"}),
    )
    with pytest.raises(oauth.GitHubDeviceFlowError, match="Device flow is not enabled"):
        oauth.request_github_device_code(client_id="cid")


# --- poll_github_device_token ---


def test_poll_returns_token_after_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            _FakeResponse({"error": "authorization_pending"}),
            _FakeResponse({"access_token": "gho_abc", "token_type": "bearer", "scope": "repo"}),
        ]
    )
    monkeypatch.setattr(oauth.httpx, "post", lambda *_a, **_k: next(responses))

    token = oauth.poll_github_device_token(
        client_id="cid",
        device_code=_device_code(),
        sleep=lambda _s: None,
        monotonic=lambda: 0.0,
    )

    assert token.access_token == "gho_abc"
    assert token.scope == "repo"


def test_poll_handles_slow_down_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    responses = iter(
        [
            _FakeResponse({"error": "slow_down", "interval": 12}),
            _FakeResponse({"access_token": "gho_xyz"}),
        ]
    )
    monkeypatch.setattr(oauth.httpx, "post", lambda *_a, **_k: next(responses))

    token = oauth.poll_github_device_token(
        client_id="cid",
        device_code=_device_code(interval=5),
        sleep=slept.append,
        monotonic=lambda: 0.0,
    )

    assert token.access_token == "gho_xyz"
    # First sleep uses the original interval, the second uses the backed-off one.
    assert slept[0] == 5.0
    assert slept[1] == 12.0


def test_poll_raises_on_access_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth.httpx, "post", lambda *_a, **_k: _FakeResponse({"error": "access_denied"})
    )
    with pytest.raises(oauth.GitHubDeviceFlowError, match="denied in the browser"):
        oauth.poll_github_device_token(
            client_id="cid",
            device_code=_device_code(),
            sleep=lambda _s: None,
            monotonic=lambda: 0.0,
        )


def test_poll_raises_when_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth.httpx,
        "post",
        lambda *_a, **_k: pytest.fail("should not poll after expiry"),
    )
    with pytest.raises(oauth.GitHubDeviceFlowError, match="expired"):
        oauth.poll_github_device_token(
            client_id="cid",
            device_code=_device_code(expires_in=0),
            sleep=lambda _s: None,
            monotonic=lambda: 100.0,
        )


# --- authorize_github_via_device_flow ---


def test_authorize_raises_without_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setattr(oauth, "DEFAULT_GITHUB_OAUTH_CLIENT_ID", "")
    with pytest.raises(oauth.GitHubDeviceFlowError, match="No GitHub OAuth client id"):
        oauth.authorize_github_via_device_flow()


def test_authorize_happy_path_invokes_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    code = _device_code()
    monkeypatch.setattr(oauth, "request_github_device_code", lambda **_k: code)
    monkeypatch.setattr(
        oauth,
        "poll_github_device_token",
        lambda **_k: oauth.GitHubDeviceToken(access_token="gho_final", scope="repo"),
    )
    prompted: list[oauth.GitHubDeviceCode] = []

    token = oauth.authorize_github_via_device_flow(
        client_id="cid",
        open_browser=False,
        on_prompt=prompted.append,
    )

    assert token.access_token == "gho_final"
    assert prompted == [code]
