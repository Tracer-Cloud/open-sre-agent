"""Integration tests for TenantMiddleware: tenant isolation and context-var correctness."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.jwt_auth import JWTClaims
from app.credentials import get_current_tenant
from app.middleware.tenant import TenantMiddleware


def _claims(tenant_id: str) -> JWTClaims:
    return JWTClaims(
        sub=tenant_id,
        organization=f"org-{tenant_id}",
        organization_slug=tenant_id,
        email=f"{tenant_id}@example.com",
        full_name="Test User",
        issuer="https://clerk.example.com",
        exp=9_999_999_999,
        iat=0,
    )


@pytest.fixture
def tenant_app() -> FastAPI:
    """Minimal FastAPI app with TenantMiddleware and a /tenant probe route."""
    app = FastAPI()
    app.add_middleware(TenantMiddleware)

    @app.get("/tenant")
    async def get_tenant_route():
        return {"tenant": get_current_tenant()}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_health_exempt_no_token(tenant_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=tenant_app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ok_exempt_no_token(tenant_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=tenant_app), base_url="http://test") as client:
        resp = await client.get("/ok")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_missing_token_returns_401(tenant_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=tenant_app), base_url="http://test") as client:
        resp = await client.get("/tenant")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing auth token"


@pytest.mark.asyncio
async def test_invalid_token_returns_401(tenant_app: FastAPI) -> None:
    from app.auth.jwt_auth import JWTVerificationError

    with patch("app.middleware.tenant.verify_jwt_async", side_effect=JWTVerificationError("bad")):
        async with AsyncClient(transport=ASGITransport(app=tenant_app), base_url="http://test") as client:
            resp = await client.get("/tenant", headers={"Authorization": "Bearer bad-token"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_sets_tenant(tenant_app: FastAPI) -> None:
    with patch("app.middleware.tenant.verify_jwt_async", return_value=_claims("acme")):
        async with AsyncClient(transport=ASGITransport(app=tenant_app), base_url="http://test") as client:
            resp = await client.get("/tenant", headers={"Authorization": "Bearer tok-acme"})
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "acme"


@pytest.mark.asyncio
async def test_concurrent_requests_get_isolated_tenant_contexts(tenant_app: FastAPI) -> None:
    """Two simultaneous requests with different tokens must each see their own tenant."""

    async def fake_verify(token: str) -> JWTClaims:
        await asyncio.sleep(0)  # yield to allow interleaving
        if token == "tok-alpha":
            return _claims("tenant-alpha")
        return _claims("tenant-beta")

    with patch("app.middleware.tenant.verify_jwt_async", side_effect=fake_verify):
        async with AsyncClient(transport=ASGITransport(app=tenant_app), base_url="http://test") as client:
            results = await asyncio.gather(
                client.get("/tenant", headers={"Authorization": "Bearer tok-alpha"}),
                client.get("/tenant", headers={"Authorization": "Bearer tok-beta"}),
            )

    assert all(r.status_code == 200 for r in results)
    tenants = {r.json()["tenant"] for r in results}
    assert tenants == {"tenant-alpha", "tenant-beta"}
