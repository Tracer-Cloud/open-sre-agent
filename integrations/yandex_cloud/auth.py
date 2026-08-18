"""IAM token minting for Yandex Cloud.

Every Yandex Cloud API call is authorised by an IAM token, and how you get one
depends on what you were given:

- a service-account authorized key, which is exchanged for a token by signing a
  short-lived JWT — the normal path for anything unattended;
- an OAuth token belonging to a user account;
- an IAM token someone already minted;
- nothing at all, when the agent runs on a Yandex Cloud VM or function whose
  metadata service hands out tokens for the attached service account.

Tokens last up to 12 hours. They are cached per credential and renewed well
before expiry, so a long investigation never stalls on a token that aged out
mid-run.
"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final, NamedTuple

import httpx

from config.constants.yandex_cloud import (
    AUTH_MODE_IAM_TOKEN,
    AUTH_MODE_METADATA,
    AUTH_MODE_OAUTH,
    AUTH_MODE_SA_KEY,
    AUTH_MODE_SA_KEY_FILE,
)
from integrations.yandex_cloud import metadata
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig
from integrations.yandex_cloud.endpoints import resolve_endpoint

_IAM_SERVICE: Final = "iam"
_IAM_FALLBACK_HOST: Final = "iam.api.cloud.yandex.net"
_TOKEN_PATH: Final = "/iam/v1/tokens"

#: Yandex only accepts PS256 for the key-exchange JWT.
_JWT_ALGORITHM: Final = "PS256"
#: The JWT itself may not outlive one hour, per the token-exchange contract.
_JWT_LIFETIME_SECONDS: Final = 3600
#: Tokens are valid for 12h; renewing at 50 minutes keeps well clear of expiry
#: without minting one per request.
_TOKEN_TTL_SECONDS: Final = 50 * 60
_REQUEST_TIMEOUT_SECONDS: Final = 15.0


class YandexCloudAuthError(RuntimeError):
    """Raised when no IAM token can be obtained from the given credentials."""


class MintedToken(NamedTuple):
    """A freshly minted IAM token and how long it should be kept."""

    token: str
    ttl_seconds: float


def _iam_token_url() -> str:
    host = resolve_endpoint(_IAM_SERVICE) or _IAM_FALLBACK_HOST
    return f"https://{host}{_TOKEN_PATH}"


def _load_authorized_key(config: YandexCloudIntegrationConfig) -> dict[str, Any]:
    """Return the service-account authorized key as a dict."""
    if config.sa_key_file:
        path = Path(config.sa_key_file).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise YandexCloudAuthError(f"Cannot read service-account key {path}: {exc}") from exc
    else:
        raw = config.sa_key
    try:
        key = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YandexCloudAuthError(
            "Service-account key is not valid JSON. Create one with "
            "`yc iam key create --output key.json --service-account-name <name>`."
        ) from exc
    if not isinstance(key, dict):
        raise YandexCloudAuthError(
            "Service-account key must be a JSON object, not a "
            f"{type(key).__name__}. Use the authorized key file as Yandex wrote it."
        )
    missing = [field for field in ("id", "service_account_id", "private_key") if not key.get(field)]
    if missing:
        raise YandexCloudAuthError(
            f"Service-account key is missing {', '.join(missing)}. It should be the "
            "authorized key JSON, not an API key or a static access key."
        )
    return dict(key)


def _signed_jwt(key: dict[str, Any], audience: str) -> str:
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - PyJWT is a core dependency
        raise YandexCloudAuthError(f"PyJWT is required to sign the key exchange: {exc}") from exc

    now = int(time.time())
    try:
        return jwt.encode(
            {
                "iss": key["service_account_id"],
                "aud": audience,
                "iat": now,
                "exp": now + _JWT_LIFETIME_SECONDS,
            },
            key["private_key"],
            algorithm=_JWT_ALGORITHM,
            headers={"kid": key["id"]},
        )
    except Exception as exc:
        raise YandexCloudAuthError(f"Could not sign the key-exchange JWT: {exc}") from exc


def _exchange(payload: dict[str, str]) -> str:
    url = _iam_token_url()
    try:
        response = httpx.post(url, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise YandexCloudAuthError(f"Could not reach the IAM token endpoint: {exc}") from exc
    if response.status_code != HTTPStatus.OK:
        raise YandexCloudAuthError(
            f"IAM token request was rejected ({response.status_code}): "
            f"{response.text.strip()[:300]}"
        )
    token = str(response.json().get("iamToken", "")).strip()
    if not token:
        raise YandexCloudAuthError("IAM token endpoint returned no token.")
    return token


def _metadata_token() -> MintedToken:
    """Return the attached service account's token, honouring its stated expiry."""
    minted = metadata.fetch_token()
    if minted is None:
        raise YandexCloudAuthError(
            "The instance metadata service did not return a token. This mode only "
            "works on a Yandex Cloud VM, function, or container with a service "
            "account attached — check that one is linked to this instance."
        )
    return MintedToken(token=minted.token, ttl_seconds=minted.ttl_seconds)


def mint_iam_token_with_ttl(config: YandexCloudIntegrationConfig) -> MintedToken:
    """Return a fresh IAM token for *config* and how long to keep it.

    Most credentials produce a token valid for up to 12 hours with no stated
    expiry, so they take the default renewal period. The metadata service does
    state one, and is taken at its word.
    """
    mode = config.auth_mode
    if mode in (AUTH_MODE_SA_KEY_FILE, AUTH_MODE_SA_KEY):
        key = _load_authorized_key(config)
        return MintedToken(
            _exchange({"jwt": _signed_jwt(key, _iam_token_url())}), _TOKEN_TTL_SECONDS
        )
    if mode == AUTH_MODE_OAUTH:
        return MintedToken(
            _exchange({"yandexPassportOauthToken": config.oauth_token}), _TOKEN_TTL_SECONDS
        )
    if mode == AUTH_MODE_IAM_TOKEN:
        # Supplied by hand and already ticking; nothing here can renew it.
        return MintedToken(config.iam_token, _TOKEN_TTL_SECONDS)
    if mode == AUTH_MODE_METADATA:
        return _metadata_token()
    raise YandexCloudAuthError(
        "No Yandex Cloud credential is configured. Run `opensre integrations setup yandex_cloud`."
    )


def mint_iam_token(config: YandexCloudIntegrationConfig) -> str:
    """Return a fresh IAM token for *config*, ignoring any cached one."""
    return mint_iam_token_with_ttl(config).token


class YandexCloudAuth:
    """Hands out IAM tokens, minting and renewing them as needed.

    Shared by every tool in the Yandex Cloud family, so it is safe to call from
    several threads at once.
    """

    def __init__(self, config: YandexCloudIntegrationConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def config(self) -> YandexCloudIntegrationConfig:
        return self._config

    def token(self) -> str:
        """Return a valid IAM token, minting a new one when the current one ages out."""
        with self._lock:
            if self._token is not None and time.monotonic() < self._expires_at:
                return self._token
            minted = mint_iam_token_with_ttl(self._config)
            self._token = minted.token
            self._expires_at = time.monotonic() + minted.ttl_seconds
            return minted.token

    def invalidate(self) -> None:
        """Drop the cached token so the next call mints a new one."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}


__all__ = [
    "MintedToken",
    "YandexCloudAuth",
    "YandexCloudAuthError",
    "mint_iam_token",
    "mint_iam_token_with_ttl",
]
