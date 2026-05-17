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
