from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.correlation.upstream import (
    LogSignal,
    MetricSeries,
)

DatadogQueryFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class DatadogCorrelationAdapter:
    def __init__(
        self,
        *,
        metric_query_fn: DatadogQueryFn,
        log_query_fn: DatadogQueryFn,
    ) -> None:
        self._metric_query_fn = metric_query_fn
        self._log_query_fn = log_query_fn

    def query_metric_series(
        self,
        *,
        metric_name: str,
        start: str,
        end: str,
    ) -> MetricSeries:
        payload = self._metric_query_fn(
            metric_name,
            {
                "from": start,
                "to": end,
            },
        )

        return MetricSeries(
            source="datadog",
            name=metric_name,
            timestamps=tuple(str(timestamp) for timestamp in payload.get("timestamps", ())),
            values=tuple(float(v) for v in payload.get("values", ())),
        )

    def query_logs(
        self,
        *,
        query: str,
        start: str,
        end: str,
    ) -> LogSignal:
        payload = self._log_query_fn(
            query,
            {
                "from": start,
                "to": end,
            },
        )

        return LogSignal(
            source="datadog",
            name=query,
            timestamps=tuple(str(timestamp) for timestamp in payload.get("timestamps", ())),
            messages=tuple(str(message) for message in payload.get("messages", ())),
        )
