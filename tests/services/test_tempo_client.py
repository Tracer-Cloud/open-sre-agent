"""Unit tests for the Grafana Tempo service client."""

from __future__ import annotations

from typing import Any

import httpx

from app.integrations.tempo import TempoConfig
from app.services.tempo.client import TempoClient


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _ErrorResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("GET", "http://localhost")

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError(
            f"error {self.status_code}",
            request=self.request,
            response=httpx.Response(self.status_code, request=self.request, text=self.text),
        )

    def json(self) -> dict[str, Any]:
        return {}


def _client() -> TempoClient:
    return TempoClient(TempoConfig(url="http://localhost:3200", api_key="token"))


def test_get_trace_requires_configuration() -> None:
    result = TempoClient(TempoConfig()).get_trace_by_id("abc")
    assert result["available"] is False
    assert "TEMPO_URL" in result["error"]


def test_get_trace_requires_trace_id() -> None:
    result = _client().get_trace_by_id("")
    assert result["available"] is False
    assert "trace_id is required" in result["error"]


def test_get_trace_by_id_parses_spans(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _FakeResponse(
            {
                "batches": [
                    {
                        "resource": {
                            "attributes": [{"key": "service.name", "value": {"stringValue": "api"}}]
                        },
                        "scopeSpans": [
                            {"spans": [{"name": "GET /x", "spanId": "s1", "attributes": []}]}
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.tempo.client.httpx.get", _fake_get)
    result = _client().get_trace_by_id("trace-1")

    assert result["available"] is True
    assert result["trace_id"] == "trace-1"
    assert result["total_spans"] == 1
    assert result["spans"][0]["service_name"] == "api"
    assert captured["url"].endswith("/api/traces/trace-1")
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_search_traces_builds_traceql(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResponse(
            {
                "traces": [
                    {
                        "traceID": "t1",
                        "rootServiceName": "api",
                        "rootTraceName": "GET /x",
                        "durationMs": 120,
                        "spanSet": {"matched": 3},
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.tempo.client.httpx.get", _fake_get)
    result = _client().search_traces(
        service="api",
        span_name="GET /x",
        min_duration_ms=100,
        tags={"http.status_code": "500"},
        limit=5,
    )

    assert result["available"] is True
    assert result["total"] == 1
    assert result["traces"][0]["trace_id"] == "t1"
    assert result["traces"][0]["matched_spans"] == 3
    assert captured["url"].endswith("/api/search")
    query = captured["params"]["q"]
    assert 'resource.service.name = "api"' in query
    assert 'name = "GET /x"' in query
    assert "duration > 100ms" in query
    assert 'span.http.status_code = "500"' in query
    assert captured["params"]["limit"] == 5


def test_search_traces_empty_query_when_no_filters(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_url: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params")
        return _FakeResponse({"traces": []})

    monkeypatch.setattr("app.services.tempo.client.httpx.get", _fake_get)
    result = _client().search_traces()
    assert result["available"] is True
    assert captured["params"]["q"] == "{}"


def test_list_services_parses_v2_tag_values(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **_kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse(
            {"tagValues": [{"type": "string", "value": "frontend"}, {"value": "cartservice"}]}
        )

    monkeypatch.setattr("app.services.tempo.client.httpx.get", _fake_get)
    result = _client().list_services()
    assert result["available"] is True
    assert result["services"] == ["frontend", "cartservice"]
    assert captured["url"].endswith("/api/v2/search/tag/resource.service.name/values")


def test_list_span_names_parses_v1_string_values(monkeypatch) -> None:
    def _fake_get(_url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse({"tagValues": ["GET /a", "POST /b"]})

    monkeypatch.setattr("app.services.tempo.client.httpx.get", _fake_get)
    result = _client().list_span_names()
    assert result["available"] is True
    assert result["span_names"] == ["GET /a", "POST /b"]


def test_search_traces_surfaces_http_error(monkeypatch) -> None:
    def _fake_get(_url: str, **_kwargs: Any) -> _ErrorResponse:
        return _ErrorResponse(403, "forbidden")

    monkeypatch.setattr("app.services.tempo.client.httpx.get", _fake_get)
    result = _client().search_traces(service="api")
    assert result["available"] is False
    assert "403" in result["error"]
