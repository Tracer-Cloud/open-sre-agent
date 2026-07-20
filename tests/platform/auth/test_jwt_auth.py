from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest

from config.config import CLERK_CONFIG_DEV, CLERK_CONFIG_PROD, CLERK_ISSUER_ENV, CLERK_JWKS_URL_ENV
from platform.auth.jwt_auth import (
    AsyncJWKSCache,
    JWTVerificationError,
    get_jwks_url_for_issuer,
    get_signing_key_from_jwks,
    get_valid_issuers,
)

_SILO_ISSUER = "https://clerk.example-silo.com"


@pytest.fixture(autouse=True)
def _no_ambient_clerk_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests set CLERK_ISSUER / CLERK_JWKS_URL explicitly; ignore the dev shell."""
    monkeypatch.delenv(CLERK_ISSUER_ENV, raising=False)
    monkeypatch.delenv(CLERK_JWKS_URL_ENV, raising=False)


@pytest.mark.asyncio
async def test_get_jwks_fetches_once_and_uses_cache_within_ttl() -> None:
    """Lock in JWKS cache behavior so refactors do not refetch per request."""
    cache = AsyncJWKSCache(_cache_ttl=3600)
    jwks_url = "https://example.com/.well-known/jwks.json"
    jwks_payload = {
        "keys": [
            {
                "kid": "kid-1",
                "kty": "RSA",
            }
        ]
    }

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = jwks_payload

    with (
        patch("platform.auth.jwt_auth.time.time", side_effect=[1000.0, 1001.0]),
        patch("platform.auth.jwt_auth.httpx.AsyncClient") as mock_async_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_async_client_cls.return_value.__aenter__.return_value = mock_client

        first = await cache.get_jwks(jwks_url)
        second = await cache.get_jwks(jwks_url)

    assert first == jwks_payload
    assert second == jwks_payload
    assert mock_client.get.await_count == 1
    response.raise_for_status.assert_called_once()


def test_get_signing_key_from_jwks_raises_on_invalid_jwk() -> None:
    """Bad JWK data should raise JWTVerificationError, not a bare Exception."""
    token = pyjwt.encode(
        {"sub": "1"},
        "secret",
        algorithm="HS256",
        headers={"kid": "bad-kid"},
    )
    jwks = {"keys": [{"kid": "bad-kid", "kty": "UNSUPPORTED"}]}

    with pytest.raises(JWTVerificationError, match="Failed to parse JWK"):
        get_signing_key_from_jwks(jwks, token)


def test_default_issuers_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    assert get_valid_issuers() == [CLERK_CONFIG_PROD.issuer]

    monkeypatch.setenv("ENV", "development")
    assert get_valid_issuers() == [CLERK_CONFIG_DEV.issuer, CLERK_CONFIG_PROD.issuer]


def test_clerk_env_override_is_accepted_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """The silo's infra-injected Clerk instance must reach verification (gap G1)."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(CLERK_ISSUER_ENV, f"{_SILO_ISSUER}/")
    monkeypatch.setenv(CLERK_JWKS_URL_ENV, f"{_SILO_ISSUER}/.well-known/jwks.json")

    issuers = get_valid_issuers()

    # Trailing slash normalized; hardcoded default kept as fallback.
    assert issuers[0] == _SILO_ISSUER
    assert CLERK_CONFIG_PROD.issuer in issuers
    assert get_jwks_url_for_issuer(_SILO_ISSUER) == f"{_SILO_ISSUER}/.well-known/jwks.json"
    # Default issuers still resolve their own JWKS URLs.
    assert get_jwks_url_for_issuer(CLERK_CONFIG_PROD.issuer) == CLERK_CONFIG_PROD.jwks_url


def test_clerk_jwks_url_defaults_from_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLERK_ISSUER_ENV, _SILO_ISSUER)

    assert get_jwks_url_for_issuer(_SILO_ISSUER) == f"{_SILO_ISSUER}/.well-known/jwks.json"
