"""Unit tests for SigNoz service client."""

from __future__ import annotations

from typing import Any

from app.integrations.signoz import SigNozConfig
from app.services.signoz.client import SigNozClient


class _FakeResult:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self.row_count = 1
        self.first_row = row


class _FakeClient:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row
        self.closed = False

    def query(self, _query: str, parameters: dict[str, Any] | None = None) -> _FakeResult:
        assert parameters is not None
        return _FakeResult(self._row)

    def close(self) -> None:
        self.closed = True


class _FakeMetricsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.row_count = len(rows)
        self.first_row = rows[0] if rows else ()

    def named_results(self) -> list[dict[str, Any]]:
        return self._rows


class _CaptureMetricsClient:
    def __init__(self) -> None:
        self.closed = False
        self.last_query = ""
        self.last_params: dict[str, Any] | None = None

    def query(self, query: str, parameters: dict[str, Any] | None = None) -> _FakeMetricsResult:
        self.last_query = query
        self.last_params = parameters or {}
        return _FakeMetricsResult([])

    def close(self) -> None:
        self.closed = True


def test_query_trace_summary_sanitizes_nan(monkeypatch) -> None:
    fake_client = _FakeClient((0, 0, float("nan"), float("nan"), float("nan"), float("nan")))
    monkeypatch.setattr("app.services.signoz.client._make_client", lambda _config: fake_client)

    config = SigNozConfig(clickhouse_host="localhost")
    result = SigNozClient(config).query_trace_summary(service="svc", time_range_minutes=60)

    assert result["total_spans"] == 0
    assert result["error_spans"] == 0
    assert result["error_rate"] == 0.0
    assert result["p99_ms"] == 0.0
    assert result["p95_ms"] == 0.0
    assert result["avg_ms"] == 0.0
    assert result["max_ms"] == 0.0
    assert fake_client.closed is True


def test_query_metrics_uses_null_safe_env_join(monkeypatch) -> None:
    fake_client = _CaptureMetricsClient()
    monkeypatch.setattr("app.services.signoz.client._make_client", lambda _config: fake_client)

    config = SigNozConfig(clickhouse_host="localhost")
    result = SigNozClient(config).query_metrics(metric_name="cpu_usage", service="svc")

    assert result["available"] is True
    assert result["metric_name"] == "cpu_usage"
    assert "coalesce(s.env, '') = coalesce(ts.env, '')" in fake_client.last_query
    assert fake_client.closed is True
