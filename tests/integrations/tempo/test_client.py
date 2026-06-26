"""Unit tests for the Grafana Tempo service client."""

from __future__ import annotations

from typing import Any

import httpx

from integrations.tempo import TempoConfig
from integrations.tempo.client import TempoClient


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _ErrorResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self._request = httpx.Request("GET", "http://localhost")

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError(
            f"error {self.status_code}",
            request=self._request,
            response=httpx.Response(self.status_code, request=self._request, text=self.text),
        )

    def json(self) -> dict[str, Any]:
        return {}


class _FakeClient:
    """Minimal httpx.get stand-in that records calls."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        return next(self._responses)


def _client() -> TempoClient:
    return TempoClient(TempoConfig(url="http://localhost:3200", api_key="token"))


def _patch_client(monkeypatch: Any, *responses: Any) -> _FakeClient:
    fake = _FakeClient(list(responses))
    monkeypatch.setattr(
        "integrations.tempo.client.httpx.get",
        fake.get,
    )
    return fake


def test_get_trace_requires_configuration() -> None:
    result = TempoClient(TempoConfig()).get_trace_by_id("abc")
    assert result["available"] is False
    assert "TEMPO_URL" in result["error"]


def test_get_trace_requires_trace_id() -> None:
    result = _client().get_trace_by_id("")
    assert result["available"] is False
    assert "trace_id is required" in result["error"]


def test_get_trace_by_id_parses_spans(monkeypatch: Any) -> None:
    payload = {
        "batches": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "api"}}]
                },
                "scopeSpans": [{"spans": [{"name": "GET /x", "spanId": "s1", "attributes": []}]}],
            }
        ]
    }
    fake = _patch_client(monkeypatch, _FakeResponse(payload))
    result = _client().get_trace_by_id("trace-1")

    assert result["available"] is True
    assert result["trace_id"] == "trace-1"
    assert result["total_spans"] == 1
    assert result["spans"][0]["service_name"] == "api"
    assert fake.calls[0]["url"].endswith("/api/traces/trace-1")


def test_get_trace_by_id_surfaces_non_dict_response(monkeypatch: Any) -> None:
    _patch_client(monkeypatch, _FakeResponse([{"traceID": "t1"}]))
    result = _client().get_trace_by_id("trace-1")
    assert result["available"] is False
    assert "Unexpected response shape" in result["error"]


def test_search_traces_builds_traceql(monkeypatch: Any) -> None:
    payload = {
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
    fake = _patch_client(monkeypatch, _FakeResponse(payload))
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
    assert fake.calls[0]["url"].endswith("/api/search")
    query = fake.calls[0]["params"]["q"]
    assert 'resource.service.name = "api"' in query
    assert 'name = "GET /x"' in query
    assert "duration > 100ms" in query
    assert 'span.http.status_code = "500"' in query
    assert fake.calls[0]["params"]["limit"] == 5


def test_search_traces_resource_tag_prefix_preserved(monkeypatch: Any) -> None:
    fake = _patch_client(monkeypatch, _FakeResponse({"traces": []}))
    _client().search_traces(
        tags={"resource.deployment.environment": "prod", "span.db.system": "postgres"},
    )
    query = fake.calls[0]["params"]["q"]
    assert 'resource.deployment.environment = "prod"' in query
    assert 'span.db.system = "postgres"' in query


def test_search_traces_empty_query_when_no_filters(monkeypatch: Any) -> None:
    fake = _patch_client(monkeypatch, _FakeResponse({"traces": []}))
    result = _client().search_traces()
    assert result["available"] is True
    assert fake.calls[0]["params"]["q"] == "{}"


def test_list_services_parses_v2_tag_values(monkeypatch: Any) -> None:
    payload = {"tagValues": [{"type": "string", "value": "frontend"}, {"value": "cartservice"}]}
    fake = _patch_client(monkeypatch, _FakeResponse(payload))
    result = _client().list_services()
    assert result["available"] is True
    assert result["services"] == ["frontend", "cartservice"]
    assert fake.calls[0]["url"].endswith("/api/v2/search/tag/resource.service.name/values")


def test_list_services_falls_back_to_v1_on_404(monkeypatch: Any) -> None:
    fake = _patch_client(
        monkeypatch,
        _ErrorResponse(404, "not found"),
        _FakeResponse({"tagValues": ["svc-a", "svc-b"]}),
    )
    result = _client().list_services()
    assert result["available"] is True
    assert result["services"] == ["svc-a", "svc-b"]
    assert fake.calls[0]["url"].endswith("/api/v2/search/tag/resource.service.name/values")
    assert fake.calls[1]["url"].endswith("/api/search/tag/resource.service.name/values")


def test_list_span_names_parses_v1_string_values(monkeypatch: Any) -> None:
    fake = _patch_client(monkeypatch, _FakeResponse({"tagValues": ["GET /a", "POST /b"]}))
    result = _client().list_span_names()
    assert result["available"] is True
    assert result["span_names"] == ["GET /a", "POST /b"]
    assert fake.calls[0]["url"].endswith("/api/v2/search/tag/name/values")


def test_search_traces_surfaces_http_error(monkeypatch: Any) -> None:
    _patch_client(monkeypatch, _ErrorResponse(403, "forbidden"))
    result = _client().search_traces(service="api")
    assert result["available"] is False
    assert "403" in result["error"]
