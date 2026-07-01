"""Tests for SigNoz tools."""

from typing import Any

from integrations.signoz.tools import (
    _metrics_is_available,
    query_signoz_logs,
    query_signoz_metrics,
    query_signoz_traces,
)


class _FakeSigNozBackend:
    """Fake backend for synthetic tests."""

    def query_logs(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_logs",
            "available": True,
            "total": 2,
            "logs": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "severity": "ERROR",
                    "severity_number": 17,
                    "message": "connection refused",
                    "trace_id": "abc123",
                    "span_id": "def456",
                    "attributes": {"http.method": "GET"},
                    "resources": {"service.name": "api"},
                },
                {
                    "timestamp": "2024-01-01T00:01:00Z",
                    "severity": "INFO",
                    "severity_number": 9,
                    "message": "request completed",
                    "trace_id": "",
                    "span_id": "",
                    "attributes": {},
                    "resources": {"service.name": "api"},
                },
            ],
        }

    def query_metrics(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_metrics",
            "available": True,
            "total": 2,
            "metric_name": kwargs.get("metric_name"),
            "resolved_metric": kwargs.get("metric_name"),
            "aggregation": kwargs.get("aggregation"),
            "metrics": [
                {
                    "interval": "2024-01-01 00:00:00",
                    "value": 42.0,
                    "metric_name": kwargs.get("metric_name"),
                    "service_name": kwargs.get("service") or "",
                },
            ],
        }

    def query_traces(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total": 1,
            "traces": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "trace_id": "abc123",
                    "span_id": "span1",
                    "name": "GET /api/health",
                    "duration_ms": 150.0,
                    "has_error": True,
                    "status_code": 2,
                    "status_code_string": "Error",
                    "http_method": "GET",
                    "http_url": "/api/health",
                    "kind_string": "Server",
                    "service_name": kwargs.get("service") or "api",
                },
            ],
        }

    def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total_spans": 100,
            "error_spans": 5,
            "error_rate": 0.05,
            "p99_ms": 250.0,
            "p95_ms": 180.0,
            "avg_ms": 120.0,
            "max_ms": 500.0,
        }


class _NoisyTracesBackend:
    """Backend returning a high-volume trace payload to exercise list compaction.

    Mirrors the real SigNoz client shape: one flat per-span row per entry
    (trace_id/span_id/name/duration_ms/has_error/...), with no nested ``spans``
    list — so the assertions exercise the trace-list cap the SigNoz path actually
    hits, not a fabricated span-nesting shape the client never emits.
    """

    def __init__(self, n_traces: int = 50) -> None:
        self._n_traces = n_traces

    def query_traces(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total": self._n_traces,
            "traces": [
                {
                    "trace_id": f"t{i}",
                    "span_id": f"s{i}",
                    "name": "GET /api/health",
                    "duration_ms": 12.3,
                    "has_error": True,
                    "service_name": "api",
                }
                for i in range(self._n_traces)
            ],
        }

    def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "error_rate": 0.1,
            "p99_ms": 300.0,
        }


class TestQuerySignozLogs:
    def test_available_with_query_api_credentials_only(self) -> None:
        from integrations.signoz.tools import _logs_is_available

        assert (
            _logs_is_available(
                {
                    "signoz": {
                        "url": "http://localhost:8080",
                        "api_key": "test-key",
                        "connection_verified": False,
                    }
                }
            )
            is True
        )

    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_logs(
            service="api",
            time_range_minutes=60,
            severity="ERROR",
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_logs"
        assert result["available"] is True
        assert result["total"] == 2
        assert len(result["logs"]) == 2
        assert len(result["error_logs"]) == 1
        assert result["error_logs"][0]["severity"] == "ERROR"

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_logs(service="api")
        assert result["source"] == "signoz_logs"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()


class TestQuerySignozMetrics:
    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_metrics(
            metric_name="cpu_usage",
            service="api",
            time_range_minutes=60,
            aggregation="avg",
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_metrics"
        assert result["available"] is True
        assert result["metric_name"] == "cpu_usage"
        assert len(result["metrics"]) == 1

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_metrics(metric_name="cpu_usage")
        assert result["source"] == "signoz_metrics"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()

    def test_available_with_metrics_api_credentials_only(self) -> None:
        assert (
            _metrics_is_available(
                {
                    "signoz": {
                        "url": "http://localhost:8080",
                        "api_key": "test-key",
                        "connection_verified": False,
                    }
                }
            )
            is True
        )


class TestQuerySignozTraces:
    def test_available_with_query_api_credentials_only(self) -> None:
        from integrations.signoz.tools import _traces_is_available

        assert (
            _traces_is_available(
                {
                    "signoz": {
                        "url": "http://localhost:8080",
                        "api_key": "test-key",
                        "connection_verified": False,
                    }
                }
            )
            is True
        )

    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_traces(
            service="api",
            time_range_minutes=60,
            error_only=True,
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_traces"
        assert result["available"] is True
        assert result["total"] == 1
        assert result["summary"]["error_rate"] == 0.05

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_traces(service="api")
        assert result["source"] == "signoz_traces"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()

    def test_high_volume_traces_compacted_to_limit(self) -> None:
        from core.tool_framework.utils.compaction import DEFAULT_TRACE_LIMIT

        backend = _NoisyTracesBackend(n_traces=50)
        raw_traces = backend.query_traces()["traces"]
        result = query_signoz_traces(service="api", signoz_backend=backend)

        assert result["available"] is True
        # Exact cap (not <=) so a regression in the trace limit is caught.
        assert len(result["traces"]) == DEFAULT_TRACE_LIMIT
        # The list is bounded to the first N rows, each passed through unchanged
        # (SigNoz rows are flat — there is no per-trace span nesting to cap).
        assert result["traces"] == raw_traces[:DEFAULT_TRACE_LIMIT]
        assert result["truncation_note"] == f"Showing {DEFAULT_TRACE_LIMIT} of 50 traces"
        # The aggregate trace summary is passed through untouched by compaction.
        assert result["summary"] == backend.query_trace_summary()

    def test_small_trace_result_is_not_truncated(self) -> None:
        backend = _NoisyTracesBackend(n_traces=3)
        raw_traces = backend.query_traces()["traces"]
        result = query_signoz_traces(service="api", signoz_backend=backend)

        # Below the cap: every row passes through, no truncation note.
        assert result["traces"] == raw_traces
        assert "truncation_note" not in result
        assert result["summary"] == backend.query_trace_summary()

    def test_unavailable_traces_result_passes_through(self) -> None:
        class _DownBackend:
            def query_traces(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "source": "signoz_traces",
                    "available": False,
                    "error": "upstream 503",
                    "traces": [],
                }

            def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
                return {"source": "signoz_traces", "available": False, "error": "upstream 503"}

        result = query_signoz_traces(service="api", signoz_backend=_DownBackend())

        assert result["available"] is False
        assert result["error"] == "upstream 503"
        assert "truncation_note" not in result

    def test_truncation_note_uses_server_total_not_fetched_count(self) -> None:
        # When the server-reported total exceeds the fetched (limit-bounded) list,
        # the truncation note and the returned `total` must both use the server
        # total (mirroring _normalize_logs_payload) — never the fetched length.
        from core.tool_framework.utils.compaction import DEFAULT_TRACE_LIMIT

        class _BigTotalBackend:
            def query_traces(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "source": "signoz_traces",
                    "available": True,
                    "total": 1000,  # server matched 1000; only 50 rows returned (limit)
                    "traces": [
                        {"trace_id": f"t{i}", "span_id": f"s{i}", "has_error": True}
                        for i in range(50)
                    ],
                }

            def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
                return {"source": "signoz_traces", "available": True, "error_rate": 0.2}

        result = query_signoz_traces(service="api", signoz_backend=_BigTotalBackend())

        assert result["total"] == 1000
        assert len(result["traces"]) == DEFAULT_TRACE_LIMIT
        assert result["truncation_note"] == f"Showing {DEFAULT_TRACE_LIMIT} of 1000 traces"
