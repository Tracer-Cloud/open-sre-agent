"""Tests for the ``POST /investigate`` endpoint on the gateway web app."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway import webapp

_LOOPBACK = ("127.0.0.1", 40000)
_REMOTE = ("203.0.113.9", 40000)


@pytest.fixture(autouse=True)
def _no_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(webapp.app, client=_LOOPBACK)


def _fake_payload() -> dict[str, Any]:
    return {
        "report": "Root cause identified.",
        "problem_md": "## Problem\nOrders pipeline timed out.",
        "root_cause": "Timeout calling downstream service.",
        "is_noise": False,
        "validity_score": 0.9,
        "tool_calls": [{"key": "logs", "tool_name": "hermes_logs", "data": {}}],
    }


def test_investigate_runs_pipeline_and_returns_report(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_investigation_payload(
        *, raw_alert: Any, investigation_metadata: Any = None, **_: Any
    ) -> dict[str, Any]:
        captured["raw_alert"] = raw_alert
        captured["investigation_metadata"] = investigation_metadata
        return _fake_payload()

    monkeypatch.setattr(webapp, "run_investigation_payload", _fake_run_investigation_payload)

    resp = client.post(
        "/investigate",
        json={
            "raw_alert": {"message": "Orders pipeline failed with timeout."},
            "alert_name": "etl-daily-orders-failure",
            "pipeline_name": "etl_daily_orders",
            "severity": "critical",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["report"] == "Root cause identified."
    assert body["root_cause"] == "Timeout calling downstream service."
    assert body["is_noise"] is False
    assert captured["raw_alert"] == {"message": "Orders pipeline failed with timeout."}
    assert captured["investigation_metadata"] == (
        "etl-daily-orders-failure",
        "etl_daily_orders",
        "critical",
    )


def test_investigate_resolves_metadata_from_raw_alert_when_overrides_missing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_investigation_payload(
        *, investigation_metadata: Any = None, **_: Any
    ) -> dict[str, Any]:
        captured["investigation_metadata"] = investigation_metadata
        return _fake_payload()

    monkeypatch.setattr(webapp, "run_investigation_payload", _fake_run_investigation_payload)

    resp = client.post(
        "/investigate",
        json={"raw_alert": {"alert_name": "High CPU", "severity": "warning"}},
    )

    assert resp.status_code == 200
    assert captured["investigation_metadata"] == ("High CPU", "unknown", "warning")


def test_investigate_missing_raw_alert_returns_422(client: TestClient) -> None:
    resp = client.post("/investigate", json={"alert_name": "x"})
    assert resp.status_code == 422


def test_investigate_pipeline_failure_returns_503_without_leaking_exception_text(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("llm unavailable at s3://internal-bucket/creds.json")

    monkeypatch.setattr(webapp, "run_investigation_payload", _boom)

    resp = client.post("/investigate", json={"raw_alert": {"alert_name": "x"}})

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "investigation failed: RuntimeError"
    assert "llm unavailable" not in body["error"]
    assert "s3://internal-bucket" not in body["error"]


def test_investigate_malformed_pipeline_result_returns_503(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """A result dict that fails InvestigateResponse validation is caught too."""

    def _malformed(**_: Any) -> dict[str, Any]:
        return {"report": None, "problem_md": "p", "root_cause": "c"}

    monkeypatch.setattr(webapp, "run_investigation_payload", _malformed)

    resp = client.post("/investigate", json={"raw_alert": {"alert_name": "x"}})

    assert resp.status_code == 503
    assert resp.json()["error"] == "investigation failed: ValidationError"


def test_investigate_non_loopback_without_token_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webapp, "run_investigation_payload", lambda **_: _fake_payload())
    remote = TestClient(webapp.app, client=_REMOTE)

    resp = remote.post("/investigate", json={"raw_alert": {"alert_name": "x"}})

    assert resp.status_code == 403


def test_investigate_token_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_ALERT_LISTENER_TOKEN", "sekret")
    monkeypatch.setattr(webapp, "run_investigation_payload", lambda **_: _fake_payload())
    remote = TestClient(webapp.app, client=_REMOTE)

    assert remote.post("/investigate", json={"raw_alert": {"alert_name": "x"}}).status_code == 401
    assert (
        remote.post(
            "/investigate",
            json={"raw_alert": {"alert_name": "x"}},
            headers={"Authorization": "Bearer sekret"},
        ).status_code
        == 200
    )


def test_investigate_body_over_cap_returns_413(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """A body larger than the cap is rejected before the pipeline runs."""
    monkeypatch.setattr(webapp, "MAX_ALERT_BODY_BYTES", 100)
    called = False

    def _should_not_run(**_: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _fake_payload()

    monkeypatch.setattr(webapp, "run_investigation_payload", _should_not_run)

    resp = client.post("/investigate", json={"raw_alert": {"blob": "x" * 500}})

    assert resp.status_code == 413
    assert called is False


def test_investigate_auth_runs_before_body_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unauthenticated caller is rejected by auth *before* the body is parsed.

    A remote caller sending an invalid body must get 403 (auth), not 422 (body
    validation). With the body bound as a Pydantic parameter the framework
    parsed and rejected it (422) before the handler's auth check ever ran --
    the pre-auth parse this fix closes.
    """
    monkeypatch.setattr(webapp, "run_investigation_payload", lambda **_: _fake_payload())
    remote = TestClient(webapp.app, client=_REMOTE)

    # Body is missing the required raw_alert field.
    resp = remote.post("/investigate", json={"alert_name": "x"})

    assert resp.status_code == 403
