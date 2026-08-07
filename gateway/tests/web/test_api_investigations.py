"""Tests for Clerk-gated async investigation API (in-memory store)."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from gateway.web import webapp
from platform.auth.jwt_auth import JWTClaims


def _claims(*, org: str = "org_test") -> JWTClaims:
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


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "gateway.web.clerk_deps.verify_jwt_async",
        AsyncMock(return_value=_claims()),
    )
    return TestClient(webapp.app)


def test_create_investigation_requires_auth() -> None:
    client = TestClient(webapp.app)
    resp = client.post("/api/investigations", json={"raw_alert": {"alert_name": "x"}})
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_create_investigation_returns_202_queued(client: TestClient) -> None:
    resp = client.post(
        "/api/investigations",
        json={"raw_alert": {"alert_name": "cpu"}, "alert_name": "cpu"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == HTTPStatus.ACCEPTED
    data = resp.json()
    assert data["status"] == "queued"
    assert data["investigation_id"]


def test_get_investigation_requires_auth() -> None:
    client = TestClient(webapp.app)
    resp = client.get("/api/investigations/any-id")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_invalid_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from platform.auth.jwt_auth import JWTVerificationError

    monkeypatch.setattr(
        "gateway.web.clerk_deps.verify_jwt_async",
        AsyncMock(side_effect=JWTVerificationError("bad signature")),
    )
    client = TestClient(webapp.app)

    resp = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer forged"},
    )

    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_empty_bearer_token_is_rejected() -> None:
    client = TestClient(webapp.app)
    resp = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer  "},
    )
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_other_org_cannot_read_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    verify = AsyncMock(return_value=_claims(org="org_a"))
    monkeypatch.setattr("gateway.web.clerk_deps.verify_jwt_async", verify)
    client = TestClient(webapp.app)

    created = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    ).json()

    verify.return_value = _claims(org="org_b")
    resp = client.get(
        f"/api/investigations/{created['investigation_id']}",
        headers={"Authorization": "Bearer fake"},
    )

    # Cross-org reads are indistinguishable from missing records.
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_orgless_token_is_rejected_on_create_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gateway.web.clerk_deps.verify_jwt_async",
        AsyncMock(return_value=_claims(org="")),
    )
    client = TestClient(webapp.app)
    headers = {"Authorization": "Bearer fake"}

    created = client.post("/api/investigations", json={"raw_alert": {}}, headers=headers)
    fetched = client.get("/api/investigations/any-id", headers=headers)

    # Org-less users must never share the empty-string namespace.
    assert created.status_code == HTTPStatus.FORBIDDEN
    assert fetched.status_code == HTTPStatus.FORBIDDEN


def test_create_accepts_workspace_id(client: TestClient) -> None:
    resp = client.post(
        "/api/investigations",
        json={"raw_alert": {}, "workspace_id": "ws_analytics"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == HTTPStatus.ACCEPTED
    assert resp.json()["status"] == "queued"


def test_get_investigation_scoped_to_org(client: TestClient) -> None:
    created = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    ).json()
    investigation_id = created["investigation_id"]

    ok = client.get(
        f"/api/investigations/{investigation_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert ok.status_code == HTTPStatus.OK
    assert ok.json()["status"] == "queued"
    assert ok.json()["report_url"] is None

    missing = client.get(
        "/api/investigations/does-not-exist",
        headers={"Authorization": "Bearer fake"},
    )
    assert missing.status_code == HTTPStatus.NOT_FOUND


def test_cancel_queued_investigation(client: TestClient) -> None:
    created = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    ).json()
    investigation_id = created["investigation_id"]

    cancelled = client.post(
        f"/api/investigations/{investigation_id}/cancel",
        headers={"Authorization": "Bearer fake"},
    )
    assert cancelled.status_code == HTTPStatus.OK
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["investigation_id"] == investigation_id

    again = client.get(
        f"/api/investigations/{investigation_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert again.status_code == HTTPStatus.OK
    assert again.json()["status"] == "cancelled"


def test_cancel_unknown_investigation_is_not_found(client: TestClient) -> None:
    resp = client.post(
        "/api/investigations/does-not-exist/cancel",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_cancel_running_investigation_returns_current_status(client: TestClient) -> None:
    from gateway.web import investigations

    created = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    ).json()
    investigation_id = created["investigation_id"]
    store = investigations._store()
    # Drain leftovers from earlier tests sharing the process-local store.
    claimed = None
    for _ in range(32):
        next_claimed = store.claim_next_queued()
        if next_claimed is None:
            break
        if next_claimed.id == investigation_id:
            claimed = next_claimed
            break
    assert claimed is not None

    resp = client.post(
        f"/api/investigations/{investigation_id}/cancel",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == HTTPStatus.CONFLICT
    assert resp.json()["status"] == "running"


def test_other_org_cannot_cancel_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    verify = AsyncMock(return_value=_claims(org="org_a"))
    monkeypatch.setattr("gateway.web.clerk_deps.verify_jwt_async", verify)
    client = TestClient(webapp.app)

    created = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    ).json()
    investigation_id = created["investigation_id"]

    verify.return_value = _claims(org="org_b")
    resp = client.post(
        f"/api/investigations/{investigation_id}/cancel",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_create_investigation_writes_security_audit(
    client: TestClient,
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path

    from gateway.core.runtime import security_audit

    path = Path(str(tmp_path)) / "security-audit.jsonl"
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    resp = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == HTTPStatus.ACCEPTED
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    assert '"investigation.create"' in lines[-1]
    assert resp.json()["investigation_id"] in lines[-1]


def test_cancel_investigation_writes_security_audit(
    client: TestClient,
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path

    from gateway.core.runtime import security_audit

    path = Path(str(tmp_path)) / "security-audit.jsonl"
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    created = client.post(
        "/api/investigations",
        json={"raw_alert": {}},
        headers={"Authorization": "Bearer fake"},
    ).json()
    investigation_id = created["investigation_id"]

    cancelled = client.post(
        f"/api/investigations/{investigation_id}/cancel",
        headers={"Authorization": "Bearer fake"},
    )
    assert cancelled.status_code == HTTPStatus.OK
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    actions = [line for line in lines if '"investigation.cancel"' in line]
    assert actions
    assert investigation_id in actions[-1]
