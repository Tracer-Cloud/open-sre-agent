from __future__ import annotations

from typing import Any

import pytest

from integrations.opensre.grafana_backend_queries import (
    query_logs_from_backend,
    query_traces_from_backend,
)
from tests.benchmarks.orcabench.execution.orca_telemetry import OrcaTelemetryBackend


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _Session:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.get_calls.append((url, kwargs))
        if url.endswith("/api/ruler/grafana/api/v1/rules"):
            return _Response({})
        if url.endswith("/api/annotations"):
            return _Response([])  # type: ignore[arg-type]
        if url.endswith("/api/services"):
            return _Response({"data": ["checkout", "frontend"]})
        if url.endswith("/api/traces") or "/api/traces/" in url:
            return _Response({"data": [{"traceID": "abc", "spans": []}]})
        return _Response(
            {
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"metric": {}, "values": []}]},
            }
        )

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.post_calls.append((url, kwargs))
        if kwargs["json"]["size"] == 0:
            return _Response(
                {
                    "aggregations": {
                        "services": {
                            "buckets": [{"key": "frontend"}, {"key": "checkout"}]
                        }
                    }
                }
            )
        return _Response(
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "log-1",
                            "sort": ["2026-04-21T12:00:00Z", "log-1"],
                            "_source": {
                                "@timestamp": "2026-04-21T12:00:00Z",
                                "body": "checkout completed",
                                "severity.text": "INFO",
                                "trace.id": "trace-123",
                                "span.id": "span-456",
                                "http.response.status_code": 200,
                                "resource.service.name": "checkout",
                            }
                        }
                    ]
                }
            }
        )


def _backend(session: _Session) -> OrcaTelemetryBackend:
    return OrcaTelemetryBackend(
        endpoint="http://frontend-proxy:8080/grafana",
        username="admin",
        password="admin",
        verify_ssl=True,
        start_time="2026-04-21T11:00:00Z",
        end_time="2026-04-21T13:00:00Z",
        session=session,  # type: ignore[arg-type]
    )


def test_prometheus_and_jaeger_calls_use_explicit_april_bounds() -> None:
    session = _Session()
    backend = _backend(session)

    metrics = backend.query_timeseries("up")
    traces = backend.query_traces("checkout", limit=7)

    assert len(metrics["data"]["result"]) == 1
    assert traces["traces"][0]["traceID"] == "abc"
    metrics_params = session.get_calls[0][1]["params"]
    assert metrics_params["start"] == 1776769200.0
    assert metrics_params["end"] == 1776776400.0
    trace_params = session.get_calls[1][1]["params"]
    assert trace_params["start"] == 1776769200000000
    assert trace_params["end"] == 1776776400000000
    assert trace_params["limit"] == 7


def test_model_selected_historical_bounds_reach_all_telemetry_backends() -> None:
    session = _Session()
    backend = _backend(session)
    historical = {
        "start_time": "2026-04-20T15:00:00Z",
        "end_time": "2026-04-20T16:00:00Z",
    }

    backend.query_timeseries("up", time_bounds=historical)
    backend.query_logs("checkout", time_bounds=historical)
    backend.query_annotations(time_bounds=historical)
    traces = backend.query_traces("checkout", time_bounds=historical)

    metric_params = session.get_calls[0][1]["params"]
    assert metric_params["start"] == 1776697200.0
    assert metric_params["end"] == 1776700800.0
    log_range = session.post_calls[0][1]["json"]["query"]["bool"]["filter"][0]["range"]
    assert log_range == {
        "@timestamp": {
            "gte": "2026-04-20T15:00:00Z",
            "lte": "2026-04-20T16:00:00Z",
        }
    }
    annotation_params = session.get_calls[1][1]["params"]
    assert annotation_params[:2] == [
        ("from", 1776697200000),
        ("to", 1776700800000),
    ]
    trace_params = session.get_calls[2][1]["params"]
    assert trace_params["start"] == 1776697200000000
    assert trace_params["end"] == 1776700800000000
    assert traces["query_window"] == {
        "start": "2026-04-20T15:00:00Z",
        "end": "2026-04-20T16:00:00Z",
    }


def test_day_lookback_is_anchored_to_simulated_current_time() -> None:
    session = _Session()
    backend = _backend(session)

    backend.query_timeseries("up", time_bounds={"lookback_minutes": 1440})

    params = session.get_calls[0][1]["params"]
    assert params["start"] == 1776690000.0
    assert params["end"] == 1776776400.0


def test_end_only_preserves_the_default_window_width() -> None:
    session = _Session()
    backend = _backend(session)

    backend.query_timeseries(
        "up",
        time_bounds={"end_time": "2026-04-20T16:00:00Z"},
    )

    params = session.get_calls[0][1]["params"]
    assert params["start"] == 1776693600.0
    assert params["end"] == 1776700800.0


def test_model_selected_bounds_cannot_read_past_simulated_current_time() -> None:
    backend = _backend(_Session())

    with pytest.raises(ValueError, match="simulated current time"):
        backend.query_timeseries(
            "up",
            time_bounds={
                "start_time": "2026-04-21T13:00:00Z",
                "end_time": "2026-04-21T14:00:00Z",
            },
        )


def test_empty_trace_service_does_not_guess_a_service() -> None:
    session = _Session()
    backend = _backend(session)

    traces = backend.query_traces("", limit=20)

    assert traces["traces"] == []
    assert session.get_calls == []
    assert session.post_calls == []


def test_probe_records_compact_bounded_jaeger_evidence() -> None:
    session = _Session()
    backend = _backend(session)

    result = backend.probe()

    assert result == {
        "query_window": {
            "start": "2026-04-21T11:00:00Z",
            "end": "2026-04-21T13:00:00Z",
        },
        "service_count": 2,
        "trace_service": "checkout",
        "trace_count": 1,
        "historical_query_window": {
            "start": "2026-04-20T13:00:00Z",
            "end": "2026-04-21T13:00:00Z",
        },
        "historical_metric_series_count": 1,
    }
    assert session.get_calls[0][1]["params"]["service"] == "checkout"
    assert session.get_calls[0][1]["params"]["limit"] == 1
    assert session.get_calls[1][1]["params"] == {
        "query": "count(up)",
        "start": 1776690000.0,
        "end": 1776776400.0,
        "step": 60,
    }


def test_opensearch_call_filters_the_same_window_and_maps_log_results() -> None:
    session = _Session()
    backend = _backend(session)

    result = backend.query_logs("checkout", limit=50)
    services = backend.query_service_names()

    assert result["data"]["result"][0]["values"][0][1] == "checkout completed"
    stream = result["data"]["result"][0]["stream"]
    assert stream["service_name"] == "checkout"
    assert stream["log_level"] == "INFO"
    assert stream["attributes"] == {
        "severity.text": "INFO",
        "trace.id": "trace-123",
        "span.id": "span-456",
        "http.response.status_code": 200,
        "resource.service.name": "checkout",
    }
    assert services == ["checkout", "frontend"]
    url, kwargs = session.post_calls[0]
    assert url.endswith("/otel-logs-*/_search")
    assert kwargs["json"]["size"] == 50
    assert kwargs["json"]["query"]["bool"]["filter"][0] == (
        {
            "range": {
                "@timestamp": {
                    "gte": "2026-04-21T11:00:00Z",
                    "lte": "2026-04-21T13:00:00Z",
                }
            }
        }
    )
    assert kwargs["json"]["query"]["bool"]["filter"][1] == {
        "term": {"resource.service.name.keyword": "checkout"}
    }
    assert "must" not in kwargs["json"]["query"]["bool"]


def test_empty_log_service_does_not_query_every_service() -> None:
    session = _Session()
    backend = _backend(session)

    result = backend.query_logs("", limit=20)

    assert result["data"]["result"] == []
    assert session.post_calls == []


def test_log_query_sort_and_cursor_reach_opensearch() -> None:
    session = _Session()
    backend = _backend(session)

    result = backend.query_logs(
        "checkout",
        limit=1,
        query='severity.text:ERROR AND body:"payment failed"',
        sort_order="desc",
        cursor=["2026-04-21T12:05:00Z", "log-2"],
    )

    body = session.post_calls[0][1]["json"]
    assert body["query"]["bool"]["must"] == [
        {"query_string": {"query": 'severity.text:ERROR AND body:"payment failed"'}}
    ]
    assert body["sort"] == [{"@timestamp": "desc"}, {"_id": "desc"}]
    assert body["search_after"] == ["2026-04-21T12:05:00Z", "log-2"]
    assert result["next_cursor"] == ["2026-04-21T12:00:00Z", "log-1"]


def test_service_discovery_honors_historical_bounds() -> None:
    session = _Session()
    backend = _backend(session)

    backend.query_service_names(
        time_bounds={
            "start_time": "2026-04-20T15:00:00Z",
            "end_time": "2026-04-20T16:00:00Z",
        }
    )

    assert session.post_calls[0][1]["json"]["query"] == {
        "range": {
            "@timestamp": {
                "gte": "2026-04-20T15:00:00Z",
                "lte": "2026-04-20T16:00:00Z",
            }
        }
    }


def test_trace_filters_and_trace_id_retrieval_reach_jaeger() -> None:
    session = _Session()
    backend = _backend(session)

    backend.query_traces(
        "checkout",
        operation="POST /checkout",
        tags={"error": True, "http.status_code": 500},
        min_duration="100ms",
        max_duration="5s",
    )
    exact = backend.query_traces("", action="get_trace", trace_id="abc")

    search_params = session.get_calls[0][1]["params"]
    assert search_params["operation"] == "POST /checkout"
    assert search_params["tags"] == '{"error":true,"http.status_code":500}'
    assert search_params["minDuration"] == "100ms"
    assert search_params["maxDuration"] == "5s"
    assert session.get_calls[1][0].endswith("/api/traces/abc")
    assert exact["traces"][0]["traceID"] == "abc"


def test_rich_controls_and_source_attributes_survive_model_facing_bridge() -> None:
    session = _Session()
    backend = _backend(session)

    logs = query_logs_from_backend(
        backend,
        service_name="checkout",
        limit=1,
        query="trace.id:trace-123",
        sort_order="desc",
    )
    trace = query_traces_from_backend(
        backend,
        service_name="checkout",
        action="get_trace",
        trace_id="abc",
    )

    assert logs["query"] == "trace.id:trace-123"
    assert logs["sort_order"] == "desc"
    assert logs["next_cursor"] == ["2026-04-21T12:00:00Z", "log-1"]
    assert logs["logs"][0]["attributes"]["trace.id"] == "trace-123"
    assert logs["logs"][0]["document_id"] == "log-1"
    assert trace["action"] == "get_trace"
    assert trace["trace_id"] == "abc"
    assert trace["traces"][0]["traceID"] == "abc"
