"""SigNoz ClickHouse query client.

Thin wrapper around ``clickhouse_connect`` that knows the SigNoz schema and
enforces read-only, timeouts, and result caps.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.integrations.clickhouse import _get_client
from app.integrations.signoz import SigNozConfig

logger = logging.getLogger(__name__)

DEFAULT_TIME_RANGE_MINUTES = 60

# Curated infrastructure metrics for V1.
# Each maps to a SigNoz metric name pattern stored in distributed_time_series_v4.
_CURATED_METRICS: dict[str, str] = {
    "cpu_usage": "system_cpu_usage",
    "memory_usage": "system_memory_usage",
    # NOTE: error_rate is intentionally omitted — signoz_calls_total counts all
    # requests regardless of status.  Use a raw metric name with a label filter
    # or query signoz_traces directly for error-rate semantics.
    "request_rate": "signoz_calls_total",
}


def _make_client(config: SigNozConfig) -> Any:
    """Create a clickhouse_connect client from SigNoz config."""
    return _get_client(config.to_clickhouse_config())


def _clamp_limit(limit: int, config: SigNozConfig) -> int:
    return max(1, min(limit, config.max_results))


def _time_bounds(minutes: int) -> tuple[datetime, datetime]:
    """Return (start, end) datetimes for the last *minutes*."""
    end = datetime.now(UTC)
    start = end - timedelta(minutes=max(1, minutes))
    return start, end


def _bucket_bounds_seconds(start: datetime, end: datetime) -> tuple[int, int]:
    """Return SigNoz ts_bucket_start bounds (seconds, with 30-minute guard band)."""
    start_sec = int(start.timestamp())
    end_sec = int(end.timestamp())
    return start_sec - 1800, end_sec


class SigNozClient:
    """Read-only SigNoz ClickHouse client."""

    def __init__(self, config: SigNozConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ logs

    def query_logs(
        self,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        severity: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query ``signoz_logs.distributed_logs_v2``."""
        effective_limit = _clamp_limit(limit, self.config)
        start, end = _time_bounds(time_range_minutes)
        bucket_start, bucket_end = _bucket_bounds_seconds(start, end)

        conditions: list[str] = [
            "timestamp >= %(start)s",
            "timestamp <= %(end)s",
            "ts_bucket_start >= %(bucket_start)s",
            "ts_bucket_start <= %(bucket_end)s",
        ]
        params: dict[str, Any] = {
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
            "bucket_start": bucket_start,
            "bucket_end": bucket_end,
            "limit": effective_limit,
        }

        if service:
            conditions.append("resources_string['service.name'] = %(service)s")
            params["service"] = service

        if severity:
            conditions.append("severity_text = %(severity)s")
            params["severity"] = severity.upper()

        where_clause = " AND ".join(conditions)

        query = (
            "SELECT "
            "  timestamp, "
            "  severity_text, "
            "  severity_number, "
            "  body, "
            "  trace_id, "
            "  span_id, "
            "  attributes_string, "
            "  resources_string "
            f"FROM signoz_logs.distributed_logs_v2 "
            f"WHERE {where_clause} "
            "ORDER BY timestamp DESC "
            "LIMIT %(limit)s"
        )

        client = _make_client(self.config)
        try:
            result = client.query(query, parameters=params)
            logs: list[dict[str, Any]] = []
            for row in result.named_results():
                logs.append(
                    {
                        "timestamp": str(row["timestamp"]),
                        "severity": row["severity_text"],
                        "severity_number": row["severity_number"],
                        "message": row["body"],
                        "trace_id": row["trace_id"] or "",
                        "span_id": row["span_id"] or "",
                        "attributes": dict(row["attributes_string"] or {}),
                        "resources": dict(row["resources_string"] or {}),
                    }
                )
            return {
                "source": "signoz_logs",
                "available": True,
                "total": len(logs),
                "logs": logs,
            }
        finally:
            client.close()

    # ---------------------------------------------------------------- metrics

    def query_metrics(
        self,
        metric_name: str,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        aggregation: str = "avg",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query ``signoz_metrics.distributed_samples_v4`` joined with ``time_series_v4``."""
        effective_limit = _clamp_limit(limit, self.config)
        start, end = _time_bounds(time_range_minutes)

        # Map curated aliases to actual metric names
        resolved_metric = _CURATED_METRICS.get(metric_name, metric_name)

        conditions = [
            "s.unix_milli >= %(start_ms)s",
            "s.unix_milli <= %(end_ms)s",
            "ts.metric_name = %(metric_name)s",
        ]
        params: dict[str, Any] = {
            "start_ms": int(start.timestamp() * 1000),
            "end_ms": int(end.timestamp() * 1000),
            "metric_name": resolved_metric,
            "limit": effective_limit,
        }

        if service:
            conditions.append("simpleJSONExtractString(ts.labels, 'service_name') = %(service)s")
            params["service"] = service

        agg_expr = "avg(s.value)"
        if aggregation == "sum":
            agg_expr = "sum(s.value)"
        elif aggregation == "max":
            agg_expr = "max(s.value)"
        elif aggregation == "min":
            agg_expr = "min(s.value)"
        elif aggregation == "count":
            agg_expr = "count(s.value)"

        where_clause = " AND ".join(conditions)

        query = (
            "SELECT "
            "  toStartOfInterval(fromUnixTimestamp64Milli(s.unix_milli), INTERVAL 1 MINUTE) AS interval, "
            f"  {agg_expr} AS value, "
            "  ts.metric_name, "
            "  simpleJSONExtractString(ts.labels, 'service_name') AS service_name "
            "FROM signoz_metrics.distributed_samples_v4 AS s "
            "INNER JOIN signoz_metrics.distributed_time_series_v4 AS ts "
            "  ON s.fingerprint = ts.fingerprint "
            " AND s.metric_name = ts.metric_name "
            " AND s.temporality = ts.temporality "
            " AND coalesce(s.env, '') = coalesce(ts.env, '') "
            f"WHERE {where_clause} "
            "GROUP BY interval, ts.metric_name, service_name "
            "ORDER BY interval ASC "
            "LIMIT %(limit)s"
        )

        client = _make_client(self.config)
        try:
            result = client.query(query, parameters=params)
            metrics: list[dict[str, Any]] = []
            for row in result.named_results():
                metrics.append(
                    {
                        "interval": str(row["interval"]),
                        "value": row["value"],
                        "metric_name": row["metric_name"],
                        "service_name": row["service_name"] or "",
                    }
                )
            return {
                "source": "signoz_metrics",
                "available": True,
                "total": len(metrics),
                "metric_name": metric_name,
                "resolved_metric": resolved_metric,
                "aggregation": aggregation,
                "metrics": metrics,
            }
        finally:
            client.close()

    # ---------------------------------------------------------------- traces

    def query_traces(
        self,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        error_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query ``signoz_traces.distributed_signoz_index_v3``."""
        effective_limit = _clamp_limit(limit, self.config)
        start, end = _time_bounds(time_range_minutes)
        bucket_start, bucket_end = _bucket_bounds_seconds(start, end)

        conditions: list[str] = [
            "timestamp >= %(start)s",
            "timestamp <= %(end)s",
            "ts_bucket_start >= %(bucket_start)s",
            "ts_bucket_start <= %(bucket_end)s",
        ]
        params: dict[str, Any] = {
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
            "bucket_start": bucket_start,
            "bucket_end": bucket_end,
            "limit": effective_limit,
        }

        if service:
            conditions.append("resource_string_service$$name = %(service)s")
            params["service"] = service

        if error_only:
            conditions.append("has_error = true")

        where_clause = " AND ".join(conditions)

        query = (
            "SELECT "
            "  timestamp, "
            "  trace_id, "
            "  span_id, "
            "  name, "
            "  duration_nano / 1000000 AS duration_ms, "
            "  has_error, "
            "  status_code, "
            "  status_code_string, "
            "  http_method, "
            "  http_url, "
            "  kind_string, "
            "  resource_string_service$$name AS service_name "
            "FROM signoz_traces.distributed_signoz_index_v3 "
            f"WHERE {where_clause} "
            "ORDER BY timestamp DESC "
            "LIMIT %(limit)s"
        )

        client = _make_client(self.config)
        try:
            result = client.query(query, parameters=params)
            traces: list[dict[str, Any]] = []
            for row in result.named_results():
                traces.append(
                    {
                        "timestamp": str(row["timestamp"]),
                        "trace_id": row["trace_id"] or "",
                        "span_id": row["span_id"] or "",
                        "name": row["name"] or "",
                        "duration_ms": row["duration_ms"],
                        "has_error": bool(row["has_error"]),
                        "status_code": row["status_code"],
                        "status_code_string": row["status_code_string"] or "",
                        "http_method": row["http_method"] or "",
                        "http_url": row["http_url"] or "",
                        "kind_string": row["kind_string"] or "",
                        "service_name": row["service_name"] or "",
                    }
                )
            return {
                "source": "signoz_traces",
                "available": True,
                "total": len(traces),
                "traces": traces,
            }
        finally:
            client.close()

    # ---------------------------------------------------------------- summary

    def query_trace_summary(
        self,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
    ) -> dict[str, Any]:
        """Return aggregate trace stats (error rate, p99 latency, call count)."""
        start, end = _time_bounds(time_range_minutes)
        bucket_start, bucket_end = _bucket_bounds_seconds(start, end)

        conditions: list[str] = [
            "timestamp >= %(start)s",
            "timestamp <= %(end)s",
            "ts_bucket_start >= %(bucket_start)s",
            "ts_bucket_start <= %(bucket_end)s",
        ]
        params: dict[str, Any] = {
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
            "bucket_start": bucket_start,
            "bucket_end": bucket_end,
        }

        if service:
            conditions.append("resource_string_service$$name = %(service)s")
            params["service"] = service

        where_clause = " AND ".join(conditions)

        query = (
            "SELECT "
            "  count() AS total_spans, "
            "  countIf(has_error = true) AS error_spans, "
            "  quantile(0.99)(duration_nano / 1000000) AS p99_ms, "
            "  quantile(0.95)(duration_nano / 1000000) AS p95_ms, "
            "  avg(duration_nano / 1000000) AS avg_ms, "
            "  max(duration_nano / 1000000) AS max_ms "
            "FROM signoz_traces.distributed_signoz_index_v3 "
            f"WHERE {where_clause}"
        )

        client = _make_client(self.config)
        try:
            result = client.query(query, parameters=params)
            row = result.first_row if result.row_count > 0 else (0, 0, 0.0, 0.0, 0.0, 0.0)
            total = int(row[0] or 0)
            errors = int(row[1] or 0)

            def _safe_float(value: Any, default: float = 0.0) -> float:
                try:
                    parsed = float(value)
                    return parsed if not math.isnan(parsed) else default
                except (TypeError, ValueError):
                    return default

            return {
                "source": "signoz_traces",
                "available": True,
                "total_spans": total,
                "error_spans": errors,
                "error_rate": round(errors / total, 4) if total else 0.0,
                "p99_ms": _safe_float(row[2]),
                "p95_ms": _safe_float(row[3]),
                "avg_ms": _safe_float(row[4]),
                "max_ms": _safe_float(row[5]),
            }
        finally:
            client.close()
