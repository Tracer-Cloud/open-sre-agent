from __future__ import annotations

from dataclasses import dataclass

from app.correlation.datadog_adapter import DatadogCorrelationAdapter
from app.correlation.upstream import (
    TopologyHint,
    UpstreamEvidenceBundle,
)


@dataclass(frozen=True)
class DatadogCorrelationQueries:
    rds_cpu_metric: str = "aws.rds.cpuutilization"
    rds_connections_metric: str = "aws.rds.database_connections"
    upstream_cpu_metric_template: str = "system.cpu.user{service:%s}"
    alb_log_query_template: str = "service:%s source:alb"
    app_log_query_template: str = "service:%s"


class DatadogUpstreamEvidenceProvider:
    def __init__(
        self,
        *,
        adapter: DatadogCorrelationAdapter,
        queries: DatadogCorrelationQueries | None = None,
    ) -> None:
        self._adapter = adapter
        self._queries = queries or DatadogCorrelationQueries()

    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        _ = alert_id

        rds_metrics = (
            self._adapter.query_metric_series(
                metric_name=self._queries.rds_cpu_metric,
                start=window_start,
                end=window_end,
            ),
            self._adapter.query_metric_series(
                metric_name=self._queries.rds_connections_metric,
                start=window_start,
                end=window_end,
            ),
        )

        upstream_metrics = (
            self._adapter.query_metric_series(
                metric_name=self._queries.upstream_cpu_metric_template % service_name,
                start=window_start,
                end=window_end,
            ),
        )

        web_request_logs = (
            self._adapter.query_logs(
                query=self._queries.alb_log_query_template % service_name,
                start=window_start,
                end=window_end,
            ),
        )

        app_logs = (
            self._adapter.query_logs(
                query=self._queries.app_log_query_template % service_name,
                start=window_start,
                end=window_end,
            ),
        )

        topology_hints = (
            TopologyHint(
                source=f"{service_name}-app",
                target="rds",
                relation="upstream_of",
            ),
        )

        return UpstreamEvidenceBundle(
            rds_metrics=rds_metrics,
            upstream_metrics=upstream_metrics,
            web_request_logs=web_request_logs,
            app_logs=app_logs,
            topology_hints=topology_hints,
            operator_hints=(),
        )
