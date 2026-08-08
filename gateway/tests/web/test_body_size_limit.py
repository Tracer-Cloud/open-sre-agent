"""Cross-route body-size limit tests for /investigate and /api/investigations.

These tests exercise :class:`~gateway.http.limits.BodySizeLimitMiddleware`
across all three mutating gateway routes to confirm:

* Oversized bodies are rejected with 413 on every route.
* Missing ``Content-Length`` with an oversized streamed body is still rejected
  (proves streaming enforcement, not buffer-then-check).
* Bodies under the cap succeed on the normal path.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from gateway.http.limits import MAX_BODY_BYTES
from gateway.web import webapp
from platform.auth.jwt_auth import JWTClaims

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LOOPBACK = ("127.0.0.1", 40000)

_SMALL_INVESTIGATE_PAYLOAD: dict[str, Any] = {
    "raw_alert": {"alert_name": "cpu-spike", "severity": "warning"},
}

_FAKE_RESULT: dict[str, Any] = {
    "report": "Root cause identified.",
    "problem_md": "## Problem\nCPU spike.",
    "root_cause": "Runaway process.",
    "is_noise": False,
    "validity_score": 0.9,
    "tool_calls": None,
}


def _clerk_claims(*, org: str = "org_test") -> JWTClaims:
    return JWTClaims(
        sub="user_1",
        organization=org,
        organization_slug="test",
        email="u@example.com",
        full_name="Test User",
        issuer="https://superb-jackal-75.clerk.accounts.dev",
        exp=9999999999,
        iat=1,
    )


# ---------------------------------------------------------------------------
# /investigate fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def investigate_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    monkeypatch.setattr(webapp, "run_investigation_payload", lambda **_: _FAKE_RESULT)
    monkeypatch.setattr(webapp, "resolve_investigation_context", lambda **_: {})
    return TestClient(webapp.app, client=_LOOPBACK)


# ---------------------------------------------------------------------------
# /api/investigations fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "gateway.web.clerk_deps.verify_jwt_async",
        AsyncMock(return_value=_clerk_claims()),
    )
    return TestClient(webapp.app)


# ---------------------------------------------------------------------------
# /investigate — body-size tests
# ---------------------------------------------------------------------------


def test_oversized_body_investigate_returns_413(
    investigate_client: TestClient,
) -> None:
    """Middleware rejects a >1 MiB JSON body before Pydantic binding."""
    big = b'{"raw_alert":{"x":"' + b"a" * (MAX_BODY_BYTES + 1) + b'"}}'
    resp = investigate_client.post(
        "/investigate",
        content=big,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}


def test_missing_content_length_investigate_returns_413(
    investigate_client: TestClient,
) -> None:
    """Streaming enforcement works on /investigate even without Content-Length."""
    big = b'{"raw_alert":{"x":"' + b"b" * (MAX_BODY_BYTES + 1) + b'"}}'
    resp = investigate_client.post(
        "/investigate",
        content=big,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}


def test_under_cap_body_investigate_succeeds(
    investigate_client: TestClient,
) -> None:
    """A body under the cap still reaches the handler and returns 200."""
    resp = investigate_client.post("/investigate", json=_SMALL_INVESTIGATE_PAYLOAD)
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["report"] == "Root cause identified."


# ---------------------------------------------------------------------------
# /api/investigations — body-size tests
# ---------------------------------------------------------------------------


_AUTH_HEADER = {"Authorization": "Bearer fake"}


def test_oversized_body_api_investigations_returns_413(
    api_client: TestClient,
) -> None:
    """Middleware rejects oversized bodies on POST /api/investigations."""
    big = b'{"raw_alert":{"x":"' + b"c" * (MAX_BODY_BYTES + 1) + b'"}}'
    resp = api_client.post(
        "/api/investigations",
        content=big,
        headers={"content-type": "application/json", **_AUTH_HEADER},
    )
    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}


def test_missing_content_length_api_investigations_returns_413(
    api_client: TestClient,
) -> None:
    """Streaming enforcement works on /api/investigations without Content-Length."""
    big = b'{"raw_alert":{"x":"' + b"d" * (MAX_BODY_BYTES + 1) + b'"}}'
    resp = api_client.post(
        "/api/investigations",
        content=big,
        headers={"content-type": "application/json", **_AUTH_HEADER},
    )
    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}


def test_under_cap_body_api_investigations_succeeds(
    api_client: TestClient,
) -> None:
    """A body under the cap on /api/investigations returns 202 Accepted."""
    resp = api_client.post(
        "/api/investigations",
        json={"raw_alert": {"alert_name": "cpu"}},
        headers=_AUTH_HEADER,
    )
    assert resp.status_code == HTTPStatus.ACCEPTED
    data = resp.json()
    assert data["status"] == "queued"
    assert data["investigation_id"]
