"""Tests for the ``POST /investigate`` endpoint on the gateway web app."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from config.constants.http import MAX_REQUEST_BODY_BYTES
from gateway.web import webapp

_LOOPBACK = ("127.0.0.1", 40000)
_REMOTE = ("203.0.113.9", 40000)


@pytest.fixture(autouse=True)
def _no_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(webapp.app, client=_LOOPBACK)


def _use_runner(monkeypatch: pytest.MonkeyPatch, runner: Any) -> None:
    """Substitute the payload runner the endpoint injects into ``investigate``."""
    monkeypatch.setattr(webapp, "run_investigation_payload", runner)


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

    def _fake_run(
        *, raw_alert: Any, investigation_metadata: Any = None, **_: Any
    ) -> dict[str, Any]:
        captured["raw_alert"] = raw_alert
        captured["investigation_metadata"] = investigation_metadata
        return _fake_payload()

    _use_runner(monkeypatch, _fake_run)

    resp = client.post(
        "/investigate",
        json={
            "raw_alert": {"message": "Orders pipeline failed with timeout."},
            "alert_name": "etl-daily-orders-failure",
            "severity": "critical",
        },
    )

    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["report"] == "Root cause identified."
    assert body["root_cause"] == "Timeout calling downstream service."
    assert body["is_noise"] is False
    assert captured["raw_alert"] == {"message": "Orders pipeline failed with timeout."}
    assert captured["investigation_metadata"] == (
        "etl-daily-orders-failure",
        "critical",
    )


def test_investigate_resolves_metadata_from_raw_alert_when_overrides_missing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(*, investigation_metadata: Any = None, **_: Any) -> dict[str, Any]:
        captured["investigation_metadata"] = investigation_metadata
        return _fake_payload()

    _use_runner(monkeypatch, _fake_run)

    resp = client.post(
        "/investigate",
        json={"raw_alert": {"alert_name": "High CPU", "severity": "warning"}},
    )

    assert resp.status_code == HTTPStatus.OK
    assert captured["investigation_metadata"] == ("High CPU", "warning")


def test_investigate_missing_raw_alert_returns_422(client: TestClient) -> None:
    resp = client.post("/investigate", json={"alert_name": "x"})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_investigate_pipeline_failure_returns_503_without_leaking_exception_text(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("llm unavailable at s3://internal-bucket/creds.json")

    _use_runner(monkeypatch, _boom)

    resp = client.post("/investigate", json={"raw_alert": {"alert_name": "x"}})

    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
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

    _use_runner(monkeypatch, _malformed)

    resp = client.post("/investigate", json={"raw_alert": {"alert_name": "x"}})

    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert resp.json()["error"] == "investigation failed: ValidationError"


def test_investigate_non_loopback_without_token_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_runner(monkeypatch, lambda **_: _fake_payload())
    remote = TestClient(webapp.app, client=_REMOTE)

    resp = remote.post("/investigate", json={"raw_alert": {"alert_name": "x"}})

    assert resp.status_code == HTTPStatus.FORBIDDEN


def test_investigate_token_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_ALERT_LISTENER_TOKEN", "sekret")
    _use_runner(monkeypatch, lambda **_: _fake_payload())
    remote = TestClient(webapp.app, client=_REMOTE)

    assert (
        remote.post("/investigate", json={"raw_alert": {"alert_name": "x"}}).status_code
        == HTTPStatus.UNAUTHORIZED
    )
    assert (
        remote.post(
            "/investigate",
            json={"raw_alert": {"alert_name": "x"}},
            headers={"Authorization": "Bearer sekret"},
        ).status_code
        == HTTPStatus.OK
    )


def test_investigate_at_capacity_returns_503(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from infrastructure.turn_host.concurrency import (
        TurnConcurrencyGate,
        reset_process_turn_gate_for_tests,
        set_process_turn_gate,
    )

    _use_runner(monkeypatch, lambda **_: _fake_payload())
    gate = TurnConcurrencyGate(1)
    assert gate.try_acquire() is True  # occupy the only slot
    set_process_turn_gate(gate)
    try:
        resp = client.post("/investigate", json={"raw_alert": {"alert_name": "x"}})
        assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "at capacity" in resp.json()["error"]
    finally:
        gate.release()
        reset_process_turn_gate_for_tests()


def test_oversized_investigate_body_is_rejected(client: TestClient) -> None:
    """Regression: /investigate took a Pydantic body, so nothing capped it.

    The cap only ever guarded /alerts, and FastAPI buffers the whole payload
    while solving the request model, so an oversized POST here was read into
    memory in full and then run.
    """
    resp = client.post(
        "/investigate",
        json={"raw_alert": {"text": "x" * (MAX_REQUEST_BODY_BYTES + 1)}},
    )

    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}
