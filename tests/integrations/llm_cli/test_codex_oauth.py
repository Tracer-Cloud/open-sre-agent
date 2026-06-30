from __future__ import annotations

import base64
import json
import socket
import stat
from pathlib import Path

import httpx
import pytest

from integrations.llm_cli import codex_oauth


def _json_response(payload: dict[str, object], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _jwt(payload: dict[str, object]) -> str:
    def _part(data: dict[str, object]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{_part({'alg': 'none'})}.{_part(payload)}.sig"


def _token_response() -> dict[str, object]:
    return {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "id_token": _jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-123",
                }
            }
        ),
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


def test_build_codex_oauth_request_uses_pkce_and_localhost_callback() -> None:
    request = codex_oauth.build_codex_oauth_request()

    assert f"client_id={codex_oauth.CODEX_OAUTH_CLIENT_ID}" in request.authorize_url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" in request.authorize_url
    assert "code_challenge_method=S256" in request.authorize_url
    assert "code_challenge=" in request.authorize_url
    assert "code=" not in request.authorize_url
    assert len(request.state) >= 32
    assert len(request.code_verifier) >= 43


def test_wait_for_codex_oauth_callback_writes_tokens_and_redirects_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    port = _free_port()
    monkeypatch.setattr(codex_oauth, "CODEX_OAUTH_PORT", port)
    request = codex_oauth.build_codex_oauth_request()

    def _open_browser(_url: str) -> bool:
        response = httpx.get(
            f"http://localhost:{port}/auth/callback",
            params={"code": "auth-code", "state": request.state},
            follow_redirects=False,
            timeout=5.0,
        )
        assert response.status_code == 302
        assert response.headers["Location"].startswith(f"http://localhost:{port}/success?")
        assert "id_token=" in response.headers["Location"]
        return True

    result = codex_oauth.wait_for_codex_oauth_callback(
        request=request,
        open_browser=_open_browser,
        post=lambda *_args, **_kwargs: _json_response(_token_response()),
        timeout_seconds=5.0,
    )

    assert result.account_id == "account-123"
    assert result.auth_path == tmp_path / "codex-home" / "auth.json"
    data = json.loads(result.auth_path.read_text(encoding="utf-8"))
    assert data["tokens"]["access_token"] == "access-token"
    captured = capsys.readouterr()
    assert "auth-code" not in captured.out
    assert "auth-code" not in captured.err
    assert "access-token" not in captured.out
    assert "access-token" not in captured.err
    assert "refresh-token" not in captured.out
    assert "refresh-token" not in captured.err


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"code": "auth-code", "state": "wrong"}, "invalid_state"),
        ({}, "missing_code"),
        ({"error": "access_denied"}, "access_denied"),
    ],
)
def test_wait_for_codex_oauth_callback_rejects_invalid_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    params: dict[str, str],
    message: str,
) -> None:
    port = _free_port()
    monkeypatch.setattr(codex_oauth, "CODEX_OAUTH_PORT", port)
    request = codex_oauth.build_codex_oauth_request()
    params = {**params, "state": params.get("state", request.state)}

    def _open_browser(_url: str) -> bool:
        response = httpx.get(
            f"http://localhost:{port}/auth/callback",
            params=params,
            timeout=5.0,
        )
        assert response.status_code == 400
        return True

    with pytest.raises(codex_oauth.CodexOAuthError, match=message):
        codex_oauth.wait_for_codex_oauth_callback(
            request=request,
            open_browser=_open_browser,
            timeout_seconds=5.0,
        )


def test_wait_for_codex_oauth_callback_stores_success_id_token_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    port = _free_port()
    monkeypatch.setattr(codex_oauth, "CODEX_OAUTH_PORT", port)
    request = codex_oauth.build_codex_oauth_request()
    id_token = _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "account-from-success",
            }
        }
    )

    def _open_browser(_url: str) -> bool:
        response = httpx.get(
            f"http://localhost:{port}/success",
            params={"id_token": id_token},
            timeout=5.0,
        )
        assert response.status_code == 200
        return True

    result = codex_oauth.wait_for_codex_oauth_callback(
        request=request,
        open_browser=_open_browser,
        timeout_seconds=5.0,
    )

    assert result.account_id == "account-from-success"
    data = json.loads(result.auth_path.read_text(encoding="utf-8"))
    assert data["tokens"]["id_token"] == id_token
    assert data["tokens"]["access_token"] == id_token
    assert data["tokens"]["refresh_token"] == ""


def test_wait_for_codex_oauth_callback_reports_port_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    monkeypatch.setattr(codex_oauth, "CODEX_OAUTH_PORT", port)
    request = codex_oauth.build_codex_oauth_request()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", port))
        sock.listen(1)
        with pytest.raises(codex_oauth.CodexOAuthError, match=f"port {port}"):
            codex_oauth.wait_for_codex_oauth_callback(
                request=request,
                open_browser=lambda _url: True,
                timeout_seconds=0.1,
            )


def test_exchange_codex_oauth_code_posts_code_without_logging_tokens() -> None:
    calls: list[dict[str, object]] = []

    def _post(*args: object, **kwargs: object) -> httpx.Response:
        calls.append({"args": args, "kwargs": kwargs})
        return _json_response(_token_response())

    result = codex_oauth.exchange_codex_oauth_code(
        code="auth-code",
        code_verifier="verifier",
        post=_post,
    )

    assert result["access_token"] == "access-token"
    data = calls[0]["kwargs"]["data"]  # type: ignore[index]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code"
    assert data["redirect_uri"] == codex_oauth.CODEX_OAUTH_REDIRECT_URI
    assert data["code_verifier"] == "verifier"


def test_exchange_codex_oauth_code_rejects_failed_response() -> None:
    def _post(*_args: object, **_kwargs: object) -> httpx.Response:
        return _json_response({"error": "invalid_grant"}, status_code=400)

    with pytest.raises(codex_oauth.CodexOAuthError, match="HTTP 400"):
        codex_oauth.exchange_codex_oauth_code(
            code="auth-code",
            code_verifier="verifier",
            post=_post,
        )


def test_codex_auth_payload_and_write_auth_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    payload = codex_oauth.codex_auth_payload(_token_response())

    assert payload["OPENAI_API_KEY"] is None
    assert payload["auth_mode"] == "chatgpt"
    assert payload["tokens"]["access_token"] == "access-token"  # type: ignore[index]
    assert payload["tokens"]["refresh_token"] == "refresh-token"  # type: ignore[index]
    assert payload["tokens"]["account_id"] == "account-123"  # type: ignore[index]

    path = codex_oauth.write_codex_auth(payload)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == payload
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_run_codex_oauth_login_writes_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    request = codex_oauth.build_codex_oauth_request()
    monkeypatch.setattr(codex_oauth, "build_codex_oauth_request", lambda: request)
    monkeypatch.setattr(
        codex_oauth,
        "wait_for_codex_oauth_callback",
        lambda **_kwargs: codex_oauth.CodexOAuthResult(
            account_id="account-123",
            auth_path=tmp_path / "codex-home" / "auth.json",
            detail="OpenAI OAuth tokens stored for Codex.",
        ),
    )

    def _post(*_args: object, **_kwargs: object) -> httpx.Response:
        raise AssertionError("token exchange should happen in the callback server")

    result = codex_oauth.run_codex_oauth_login(
        open_browser=lambda _url: True,
        post=_post,
    )

    assert result.account_id == "account-123"
    assert result.auth_path == tmp_path / "codex-home" / "auth.json"
    assert "OpenAI OAuth tokens stored" in result.detail
