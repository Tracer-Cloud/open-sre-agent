from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from integrations.config_models import ROOTLY_JSON_API_CONTENT_TYPE, RootlyIntegrationConfig
from integrations.rootly.client import (
    RootlyClient,
    make_rootly_client,
    normalize_visibility,
)

_TOKEN = "rootly-secret-token"


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    response.status_code = HTTPStatus.OK
    return response


def _http_error(status: int, text: str) -> httpx.HTTPStatusError:
    response = MagicMock()
    response.status_code = status
    response.text = text
    return httpx.HTTPStatusError("boom", request=MagicMock(), response=response)


@pytest.fixture
def client() -> RootlyClient:
    return RootlyClient(RootlyIntegrationConfig(api_token=_TOKEN))


def test_headers_use_json_api_content_type(client: RootlyClient) -> None:
    """A plain ``application/json`` body is rejected by Rootly outright."""
    headers = client.config.headers

    assert headers["Content-Type"] == ROOTLY_JSON_API_CONTENT_TYPE
    assert headers["Accept"] == ROOTLY_JSON_API_CONTENT_TYPE
    assert headers["Authorization"] == f"Bearer {_TOKEN}"


def test_config_rejects_non_loopback_http_base_url() -> None:
    with pytest.raises(ValueError, match="https"):
        RootlyIntegrationConfig(api_token=_TOKEN, base_url="http://169.254.169.254/latest")


@pytest.mark.parametrize("value", ["", "   ", "not-a-number", "0", "-4"])
def test_timeout_falls_back_instead_of_failing_the_integration(value: str) -> None:
    """An empty Helm value must not take the whole integration down."""
    config = RootlyIntegrationConfig.model_validate({"api_token": _TOKEN, "timeout_seconds": value})

    assert config.timeout_seconds == 15.0


def test_list_incidents_flattens_envelope_and_reports_truncation(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, params))
        return {
            "data": [
                {
                    "id": "42",
                    "type": "incidents",
                    "attributes": {
                        "title": "Checkout degraded",
                        "status": "started",
                        "severity": {"name": "SEV1"},
                        "url": "https://rootly.example/incidents/42",
                    },
                },
                "junk-not-a-dict",
            ],
            "meta": {"total_count": 7},
        }

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.list_incidents(status="started", page_size=1)

    assert result["success"] is True
    assert result["incidents"][0]["title"] == "Checkout degraded"
    assert result["incidents"][0]["severity"] == "SEV1"
    assert result["returned"] == 1
    assert result["total"] == 7
    assert result["truncated"] is True
    assert calls[0][0] == "/v1/incidents"
    assert calls[0][1]["sort"] == "-created_at"
    assert calls[0][1]["filter[status]"] == "started"


def test_list_incident_events_sorts_oldest_first(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reversing a timeline makes the model read the resolution before the trigger."""
    captured: dict[str, Any] = {}

    def fake_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured.update({"path": path, "params": params})
        return {"data": []}

    monkeypatch.setattr(client, "_get", fake_get)

    client.list_incident_events("42")

    assert captured["path"] == "/v1/incidents/42/events"
    assert captured["params"]["sort"] == "occurred_at"


def test_page_size_is_clamped_to_the_documented_maximum(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(_path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured.update(params)
        return {"data": []}

    monkeypatch.setattr(client, "_get", fake_get)

    client.list_incidents(page_size=5000)

    assert captured["page[size]"] == 50


def test_rate_limit_returns_a_generic_message(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(_path: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise _http_error(HTTPStatus.TOO_MANY_REQUESTS, f"quota for {_TOKEN} exhausted")

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.list_incidents()

    assert result["success"] is False
    assert "rate limit" in result["error"]
    assert _TOKEN not in result["error"]


def test_http_error_detail_is_redacted(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(_path: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise _http_error(HTTPStatus.FORBIDDEN, f"Authorization: Bearer {_TOKEN} is not permitted")

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_incident("42")

    assert result["success"] is False
    assert _TOKEN not in result["error"]
    assert "403" in result["error"]


def test_probe_failure_never_echoes_the_token(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(_path: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"connect failed using token {_TOKEN}")

    monkeypatch.setattr(client, "_get", fake_get)

    probe = client.probe_access()

    assert probe.ok is False
    assert _TOKEN not in probe.detail


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("external", "external"),
        ("EXTERNAL", "external"),
        ("internal", "internal"),
        ("externel", "internal"),
        ("", "internal"),
        (None, "internal"),
    ],
)
def test_visibility_defaults_to_internal(supplied: str | None, expected: str) -> None:
    """A typo must not publish an agent-written note to a customer-facing timeline."""
    assert normalize_visibility(supplied) == expected


def test_post_timeline_event_sends_a_json_api_body(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    http_client = MagicMock()
    http_client.post.return_value = _response(
        {
            "data": {
                "id": "ev-1",
                "attributes": {
                    "event": "Root cause: bad deploy.",
                    "visibility": "internal",
                    "occurred_at": "2026-01-01T00:00:00Z",
                },
            }
        }
    )
    monkeypatch.setattr(client, "_get_client", lambda: http_client)

    result = client.post_timeline_event("42", event="Root cause: bad deploy.", visibility="oops")

    assert result["success"] is True
    assert result["event_id"] == "ev-1"
    (path,) = http_client.post.call_args.args
    assert path == "/v1/incidents/42/events"
    body = http_client.post.call_args.kwargs["json"]
    assert body["data"]["type"] == "incident_events"
    assert body["data"]["attributes"]["visibility"] == "internal"
    # Rootly stamps the creation time; a model-guessed timestamp would be wrong.
    assert "occurred_at" not in body["data"]["attributes"]


def test_post_timeline_event_rejects_empty_text(client: RootlyClient) -> None:
    result = client.post_timeline_event("42", event="   ")

    assert result["success"] is False


def test_make_client_returns_none_without_a_token() -> None:
    assert make_rootly_client("") is None
    assert make_rootly_client(None) is None


def test_make_client_allows_a_loopback_base_url() -> None:
    created = make_rootly_client(_TOKEN, base_url="http://localhost:8080")

    assert created is not None
    assert created.config.base_url == "http://localhost:8080"
