from __future__ import annotations

from typing import Any

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
        if url.endswith("/api/traces"):
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
                            "_source": {
                                "@timestamp": "2026-04-21T12:00:00Z",
                                "body": "checkout completed",
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
    assert trace_params["limit"] == 5


def test_empty_trace_service_uses_bounded_discovery() -> None:
    session = _Session()
    backend = _backend(session)

    traces = backend.query_traces("", limit=20)

    assert traces["traces"][0]["traceID"] == "abc"
    _, trace_kwargs = session.get_calls[0]
    assert trace_kwargs["params"]["service"] == "checkout"
    assert trace_kwargs["params"]["limit"] == 5


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
    }
    assert session.get_calls[0][1]["params"]["service"] == "checkout"
    assert session.get_calls[0][1]["params"]["limit"] == 1


def test_opensearch_call_filters_the_same_window_and_maps_log_results() -> None:
    session = _Session()
    backend = _backend(session)

    result = backend.query_logs("checkout", limit=50)
    services = backend.query_service_names()

    assert result["data"]["result"][0]["values"][0][1] == "checkout completed"
    assert services == ["checkout", "frontend"]
    url, kwargs = session.post_calls[0]
    assert url.endswith("/otel-logs-*/_search")
    assert kwargs["json"]["size"] == 20
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
