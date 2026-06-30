"""OpenSRE-managed OpenAI Codex OAuth browser login."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_PORT = 1455
CODEX_OAUTH_CALLBACK_PATH = "/auth/callback"
CODEX_OAUTH_REDIRECT_URI = f"http://localhost:{CODEX_OAUTH_PORT}{CODEX_OAUTH_CALLBACK_PATH}"
CODEX_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_OAUTH_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
CODEX_OAUTH_AUTH_MODE = "chatgpt"
CODEX_OAUTH_TIMEOUT_SECONDS = 300.0


class CodexOAuthError(RuntimeError):
    """Raised when OpenSRE-managed Codex OAuth login fails."""


@dataclass(frozen=True)
class CodexOAuthResult:
    """Result of a completed Codex OAuth login."""

    account_id: str
    auth_path: Path
    detail: str


@dataclass(frozen=True)
class _OAuthRequest:
    state: str
    code_verifier: str
    authorize_url: str


@dataclass(frozen=True)
class _CallbackResult:
    login: CodexOAuthResult | None = None
    error: str = ""
    error_description: str = ""


def codex_home() -> Path:
    """Return the Codex home directory used for auth persistence."""
    override = os.getenv("CODEX_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def codex_auth_path() -> Path:
    """Return the Codex-compatible auth file path."""
    return codex_home() / "auth.json"


def _base64_url_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _new_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _pkce_challenge(verifier: str) -> str:
    return _base64_url_no_padding(hashlib.sha256(verifier.encode("ascii")).digest())


def build_codex_oauth_request() -> _OAuthRequest:
    """Build the Codex OAuth authorize request with state and PKCE."""
    state = secrets.token_urlsafe(32)
    verifier = _new_pkce_verifier()
    params = {
        "response_type": "code",
        "client_id": CODEX_OAUTH_CLIENT_ID,
        "redirect_uri": CODEX_OAUTH_REDIRECT_URI,
        "scope": CODEX_OAUTH_SCOPE,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "codex_cli_rs",
    }
    return _OAuthRequest(
        state=state,
        code_verifier=verifier,
        authorize_url=f"{CODEX_OAUTH_AUTHORIZE_URL}?{urlencode(params)}",
    )


class _CallbackHTTPServer(ThreadingHTTPServer):
    expected_state: str
    code_verifier: str
    post: Callable[..., httpx.Response]
    callback_result: _CallbackResult | None
    callback_event: threading.Event


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/success":
            if self.server.callback_result is None:
                self._handle_success_token_callback(parsed.query)
                return
            self._write_page(200, "OpenSRE OAuth login completed. You can close this tab.")
            return
        if parsed.path != CODEX_OAUTH_CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        received_state = query.get("state", [""])[0]
        if received_state != self.server.expected_state:
            self.server.callback_result = _CallbackResult(
                error="invalid_state",
                error_description="OAuth callback state did not match the login request.",
            )
            self._write_page(
                400,
                "OpenSRE OAuth login failed. Return to the terminal and retry.",
            )
            self.server.callback_event.set()
            return

        provider_error = query.get("error", [""])[0]
        if provider_error:
            self.server.callback_result = _CallbackResult(
                error=provider_error,
                error_description=query.get("error_description", [""])[0],
            )
            self._write_page(
                400,
                "OpenSRE OAuth login was not completed. Return to the terminal.",
            )
            self.server.callback_event.set()
            return

        code = query.get("code", [""])[0].strip()
        if not code:
            self.server.callback_result = _CallbackResult(
                error="missing_code",
                error_description="OAuth callback did not include an authorization code.",
            )
            self._write_page(
                400,
                "OpenSRE OAuth login failed. Return to the terminal and retry.",
            )
            self.server.callback_event.set()
            return

        try:
            token_response = exchange_codex_oauth_code(
                code=code,
                code_verifier=self.server.code_verifier,
                post=self.server.post,
            )
            result = persist_codex_auth_tokens(token_response)
        except CodexOAuthError as exc:
            self.server.callback_result = _CallbackResult(
                error="token_exchange_failed",
                error_description=str(exc),
            )
            self._write_page(
                400,
                "OpenSRE OAuth login failed while saving credentials. Return to the terminal.",
            )
            self.server.callback_event.set()
            return

        self.server.callback_result = _CallbackResult(login=result)
        self._redirect(_compose_success_url(token_response))
        self.server.callback_event.set()

    def log_message(self, _format: str, *_args: object) -> None:
        """Suppress callback URL logging so codes and state are not emitted."""

    def _handle_success_token_callback(self, query_string: str) -> None:
        query = parse_qs(query_string, keep_blank_values=True)
        id_token = query.get("id_token", [""])[0].strip()
        if not id_token:
            self.server.callback_result = _CallbackResult(
                error="missing_token",
                error_description="OAuth success callback did not include an id_token.",
            )
            self._write_page(
                400,
                "OpenSRE OAuth login failed. Return to the terminal and retry.",
            )
            self.server.callback_event.set()
            return

        token_response = {
            "access_token": query.get("access_token", [id_token])[0].strip() or id_token,
            "refresh_token": query.get("refresh_token", [""])[0].strip(),
            "id_token": id_token,
        }
        account_id = query.get("account_id", [""])[0].strip()
        if account_id:
            token_response["account_id"] = account_id
        try:
            result = persist_codex_auth_tokens(
                token_response,
                require_refresh_token=False,
                detail_prefix="OpenAI OAuth success token stored",
            )
        except CodexOAuthError as exc:
            self.server.callback_result = _CallbackResult(
                error="token_persist_failed",
                error_description=str(exc),
            )
            self._write_page(
                400,
                "OpenSRE OAuth login failed while saving credentials. Return to the terminal.",
            )
            self.server.callback_event.set()
            return

        self.server.callback_result = _CallbackResult(login=result)
        self._write_page(200, "OpenSRE OAuth login completed. You can close this tab.")
        self.server.callback_event.set()

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_page(self, status: int, message: str) -> None:
        body = (
            '<!doctype html><html><head><meta charset="utf-8">'
            "<title>OpenSRE OAuth</title></head><body>"
            f"<p>{message}</p></body></html>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def wait_for_codex_oauth_callback(
    *,
    request: _OAuthRequest,
    open_browser: Callable[[str], bool] = webbrowser.open,
    post: Callable[..., httpx.Response] = httpx.post,
    timeout_seconds: float = CODEX_OAUTH_TIMEOUT_SECONDS,
) -> CodexOAuthResult:
    """Open the browser and wait for OpenAI to redirect back with Codex auth."""
    event = threading.Event()
    try:
        server = _CallbackHTTPServer(("localhost", CODEX_OAUTH_PORT), _CallbackHandler)
    except OSError as exc:
        raise CodexOAuthError(
            f"Could not bind localhost:{CODEX_OAUTH_PORT}. "
            f"Free port {CODEX_OAUTH_PORT}, then retry OpenAI OAuth login."
        ) from exc

    server.expected_state = request.state
    server.code_verifier = request.code_verifier
    server.post = post
    server.callback_result = None
    server.callback_event = event

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not open_browser(request.authorize_url):
            raise CodexOAuthError(
                "Could not open the browser automatically. "
                f"Open this URL manually to continue OAuth login: {request.authorize_url}"
            )
        if not event.wait(timeout_seconds):
            raise CodexOAuthError("Timed out waiting for OpenAI OAuth callback.")
        result = server.callback_result
        if result is None:
            raise CodexOAuthError("OAuth callback completed without a result.")
        if result.error:
            detail = f": {result.error_description}" if result.error_description else ""
            raise CodexOAuthError(f"OpenAI OAuth callback failed ({result.error}){detail}.")
        if result.login is None:
            raise CodexOAuthError("OAuth callback completed without stored Codex auth.")
        return result.login
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def exchange_codex_oauth_code(
    *,
    code: str,
    code_verifier: str,
    post: Callable[..., httpx.Response] = httpx.post,
) -> dict[str, object]:
    """Exchange an authorization code for OpenAI OAuth tokens."""
    try:
        response = post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CODEX_OAUTH_CLIENT_ID,
                "code": code,
                "redirect_uri": CODEX_OAUTH_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise CodexOAuthError(f"OpenAI OAuth token exchange failed: {exc}") from exc
    if response.status_code != 200:
        raise CodexOAuthError(
            f"OpenAI OAuth token exchange failed with HTTP {response.status_code}."
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise CodexOAuthError("OpenAI OAuth token exchange returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise CodexOAuthError("OpenAI OAuth token exchange returned an unexpected payload.")
    return data


def _decode_jwt_payload(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded)
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _account_id_from_token_payload(payload: Mapping[str, object]) -> str:
    auth_claim = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, Mapping):
        chatgpt_account_id = auth_claim.get("chatgpt_account_id")
        if isinstance(chatgpt_account_id, str) and chatgpt_account_id.strip():
            return chatgpt_account_id.strip()
    for key in ("account_id", "user_id", "sub"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _required_token(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CodexOAuthError(f"OpenAI OAuth token response did not include {key}.")
    return value.strip()


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def codex_auth_payload(
    token_response: Mapping[str, object],
    *,
    require_refresh_token: bool = True,
) -> dict[str, object]:
    """Convert OpenAI's token response to Codex's auth.json shape."""
    access_token = _required_token(token_response, "access_token")
    if require_refresh_token:
        refresh_token = _required_token(token_response, "refresh_token")
    else:
        refresh_token = str(token_response.get("refresh_token") or "").strip()
    id_token = _required_token(token_response, "id_token")
    account_id = ""
    raw_account_id = token_response.get("account_id")
    if isinstance(raw_account_id, str):
        account_id = raw_account_id.strip()
    if not account_id:
        account_id = _account_id_from_token_payload(_decode_jwt_payload(id_token))
    if not account_id:
        raise CodexOAuthError("Could not derive ChatGPT account id from OpenAI OAuth tokens.")

    return {
        "OPENAI_API_KEY": None,
        "auth_mode": CODEX_OAUTH_AUTH_MODE,
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "account_id": account_id,
        },
        "last_refresh": _utc_timestamp(),
    }


def persist_codex_auth_tokens(
    token_response: Mapping[str, object],
    *,
    require_refresh_token: bool = True,
    detail_prefix: str = "OpenAI OAuth tokens stored",
) -> CodexOAuthResult:
    """Persist an OpenAI token response in Codex-compatible auth.json format."""
    payload = codex_auth_payload(
        token_response,
        require_refresh_token=require_refresh_token,
    )
    auth_path = write_codex_auth(payload)
    account_id = str(payload["tokens"]["account_id"])  # type: ignore[index]
    return CodexOAuthResult(
        account_id=account_id,
        auth_path=auth_path,
        detail=f"{detail_prefix} for Codex at {auth_path}.",
    )


def _compose_success_url(token_response: Mapping[str, object]) -> str:
    id_token = _required_token(token_response, "id_token")
    access_token = str(token_response.get("access_token") or "")
    token_claims = _decode_jwt_payload(id_token)
    access_claims = _decode_jwt_payload(access_token)
    params = {
        "id_token": id_token,
        "needs_setup": str(
            bool(token_claims.get("is_org_owner"))
            and not bool(token_claims.get("completed_platform_onboarding"))
        ).lower(),
        "org_id": str(token_claims.get("organization_id") or ""),
        "project_id": str(token_claims.get("project_id") or ""),
        "plan_type": str(access_claims.get("chatgpt_plan_type") or ""),
        "platform_url": "https://platform.openai.com",
    }
    return f"http://localhost:{CODEX_OAUTH_PORT}/success?{urlencode(params)}"


def write_codex_auth(payload: Mapping[str, object], *, path: Path | None = None) -> Path:
    """Atomically write Codex auth.json with owner-only permissions."""
    auth_path = path or codex_auth_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        auth_path.parent.chmod(0o700)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{auth_path.name}.",
        suffix=".tmp",
        dir=str(auth_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, auth_path)
        with suppress(OSError):
            auth_path.chmod(0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return auth_path


def run_codex_oauth_login(
    *,
    open_browser: Callable[[str], bool] = webbrowser.open,
    post: Callable[..., httpx.Response] = httpx.post,
    timeout_seconds: float = CODEX_OAUTH_TIMEOUT_SECONDS,
) -> CodexOAuthResult:
    """Complete the OpenSRE-managed Codex OAuth flow and persist auth.json."""
    request = build_codex_oauth_request()
    return wait_for_codex_oauth_callback(
        request=request,
        open_browser=open_browser,
        post=post,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "CODEX_OAUTH_CALLBACK_PATH",
    "CODEX_OAUTH_CLIENT_ID",
    "CODEX_OAUTH_PORT",
    "CODEX_OAUTH_REDIRECT_URI",
    "CodexOAuthError",
    "CodexOAuthResult",
    "build_codex_oauth_request",
    "codex_auth_path",
    "codex_auth_payload",
    "exchange_codex_oauth_code",
    "persist_codex_auth_tokens",
    "run_codex_oauth_login",
    "wait_for_codex_oauth_callback",
    "write_codex_auth",
]
