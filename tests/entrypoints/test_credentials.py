"""Tests for the tenant credential onboarding API."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

_ADMIN_SECRET = "test-admin-secret-32bytes-longenough"


def _make_admin_token(tenant_id: str | None = None, role: str = "admin", expired: bool = False) -> str:
    exp = int(time.time()) + (-10 if expired else 3600)
    payload: dict[str, Any] = {"sub": tenant_id or "t1", "role": role, "exp": exp}
    if tenant_id:
        payload["tenant_id"] = tenant_id
    return jwt.encode(payload, _ADMIN_SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_JWT_SECRET", _ADMIN_SECRET)
    monkeypatch.setenv("CREDENTIAL_BACKEND", "env")


@pytest.fixture()
def client() -> TestClient:
    from app.webapp import app as _app

    return TestClient(_app, raise_server_exceptions=False)


@pytest.fixture()
def mock_sm():
    """Patch boto3.client so no real AWS calls happen."""
    import botocore.exceptions  # type: ignore[import-untyped]

    mock = MagicMock()
    mock.get_secret_value.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}}, "GetSecretValue"
    )
    with patch("app.routers.credentials._sm_client", return_value=mock):
        yield mock


# ---------------------------------------------------------------------------
# POST /credentials
# ---------------------------------------------------------------------------


class TestStoreCredentials:
    def test_stores_keys_and_returns_201(self, client: TestClient, mock_sm: MagicMock) -> None:
        mock_sm.create_secret.side_effect = None
        token = _make_admin_token("acme")
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "datadog", "credentials": {"DD_API_KEY": "k1", "DD_APP_KEY": "k2", "DD_SITE": "datadoghq.eu"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["integration"] == "datadog"
        assert set(body["keys_stored"]) == {"DD_API_KEY", "DD_APP_KEY", "DD_SITE"}
        assert mock_sm.create_secret.call_count == 3

    def test_unknown_integration_returns_400(self, client: TestClient) -> None:
        token = _make_admin_token("acme")
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "nonexistent", "credentials": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "Unknown integration" in resp.json()["detail"]

    def test_unexpected_keys_returns_400(self, client: TestClient) -> None:
        token = _make_admin_token("acme")
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "slack", "credentials": {"SLACK_BOT_TOKEN": "xoxb", "EXTRA_KEY": "bad"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "Unexpected keys" in resp.json()["detail"]

    def test_missing_auth_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "slack", "credentials": {"SLACK_BOT_TOKEN": "xoxb"}},
        )
        assert resp.status_code == 401

    def test_wrong_tenant_returns_403(self, client: TestClient) -> None:
        token = _make_admin_token("other-tenant")
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "slack", "credentials": {"SLACK_BOT_TOKEN": "xoxb"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_expired_token_returns_401(self, client: TestClient) -> None:
        token = _make_admin_token("acme", expired=True)
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "slack", "credentials": {"SLACK_BOT_TOKEN": "xoxb"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_non_admin_role_returns_403(self, client: TestClient) -> None:
        token = _make_admin_token("acme", role="viewer")
        resp = client.post(
            "/api/v1/tenants/acme/credentials",
            json={"integration": "slack", "credentials": {"SLACK_BOT_TOKEN": "xoxb"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /credentials/health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def _make_sm_with_creds(self, creds: dict[str, str]) -> MagicMock:
        """Return a mock SM client that resolves the given key→value map."""
        import botocore.exceptions  # type: ignore[import-untyped]

        mock = MagicMock()

        def _get(SecretId: str) -> dict[str, str]:
            key = SecretId.split("/")[-1]
            if key in creds:
                return {"SecretString": creds[key]}
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
                "GetSecretValue",
            )

        mock.get_secret_value.side_effect = _get
        return mock

    def test_returns_missing_when_no_creds_stored(self, client: TestClient) -> None:
        import botocore.exceptions

        mock = MagicMock()
        mock.get_secret_value.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}}, "GetSecretValue"
        )
        token = _make_admin_token("acme")
        with patch("app.routers.credentials._sm_client", return_value=mock):
            resp = client.get(
                "/api/v1/tenants/acme/credentials/health",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        for integration in ("datadog", "grafana", "kubernetes", "slack", "pagerduty", "aws"):
            assert body[integration] == "missing"

    def test_returns_ok_for_datadog_when_ping_passes(self, client: TestClient) -> None:
        mock_sm = self._make_sm_with_creds(
            {"DD_API_KEY": "k", "DD_APP_KEY": "a", "DD_SITE": "datadoghq.com"}
        )
        token = _make_admin_token("acme")
        import app.routers.credentials as creds_mod

        with (
            patch("app.routers.credentials._sm_client", return_value=mock_sm),
            patch.dict(creds_mod._PING_FUNCTIONS, {"datadog": MagicMock(return_value="ok")}),
        ):
            resp = client.get(
                "/api/v1/tenants/acme/credentials/health",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["datadog"] == "ok"

    def test_returns_auth_error_for_bad_datadog_creds(self, client: TestClient) -> None:
        mock_sm = self._make_sm_with_creds(
            {"DD_API_KEY": "bad", "DD_APP_KEY": "bad", "DD_SITE": "datadoghq.com"}
        )
        token = _make_admin_token("acme")
        import app.routers.credentials as creds_mod

        with (
            patch("app.routers.credentials._sm_client", return_value=mock_sm),
            patch.dict(creds_mod._PING_FUNCTIONS, {"datadog": MagicMock(return_value="auth_error")}),
        ):
            resp = client.get(
                "/api/v1/tenants/acme/credentials/health",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["datadog"] == "auth_error"

    def test_missing_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tenants/acme/credentials/health")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /credentials/{integration}
# ---------------------------------------------------------------------------


class TestDeleteCredentials:
    def test_deletes_all_keys_for_integration(self, client: TestClient, mock_sm: MagicMock) -> None:
        mock_sm.delete_secret.return_value = {}
        token = _make_admin_token("acme")
        resp = client.delete(
            "/api/v1/tenants/acme/credentials/datadog",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204
        assert mock_sm.delete_secret.call_count == 3  # DD_API_KEY, DD_APP_KEY, DD_SITE

    def test_unknown_integration_returns_400(self, client: TestClient) -> None:
        token = _make_admin_token("acme")
        resp = client.delete(
            "/api/v1/tenants/acme/credentials/unknown",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

    def test_already_deleted_secrets_are_idempotent(self, client: TestClient) -> None:
        import botocore.exceptions

        mock = MagicMock()
        mock.delete_secret.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, "DeleteSecret"
        )
        token = _make_admin_token("acme")
        with patch("app.routers.credentials._sm_client", return_value=mock):
            resp = client.delete(
                "/api/v1/tenants/acme/credentials/slack",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 204

    def test_missing_auth_returns_401(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/tenants/acme/credentials/datadog")
        assert resp.status_code == 401
