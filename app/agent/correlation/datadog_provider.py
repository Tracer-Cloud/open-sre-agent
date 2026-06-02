from __future__ import annotations

from dataclasses import dataclass

from app.agent.correlation.datadog_adapter import DatadogCorrelationAdapter
from app.agent.correlation.upstream import (
    TopologyHint,
    UpstreamEvidenceBundle,
)


@dataclass(frozen=True)
class DatadogCorrelationQueries:
    rds_cpu_metric: str = "aws.rds.cpuutilization"
    rds_connections_metric: str = "aws.rds.database_connections"
    rds_scope_tag: str = "dbinstanceidentifier"
    upstream_cpu_metric_template: str = "system.cpu.user{service:%s}"
    alb_log_query_template: str = "service:%s source:alb"
    app_log_query_template: str = "service:%s"
    upstream_service_names: tuple[str, ...] = ()


def _scoped_rds_metric(
    metric_name: str,
    *,
    target_resource: str,
    scope_tag: str,
) -> str:
    metric = metric_name.strip()

    if not target_resource or target_resource == "unknown-rds":
        return metric

    if "{" in metric and "}" in metric:
        prefix, _, rest = metric.partition("{")
        tags, _, suffix = rest.partition("}")

        if f"{scope_tag}:" in tags:
            return metric

        scoped_tags = (
            f"{tags},{scope_tag}:{target_resource}" if tags else f"{scope_tag}:{target_resource}"
        )
        return f"{prefix}{{{scoped_tags}}}{suffix}"

    return f"{metric}{{{scope_tag}:{target_resource}}}"


class DatadogUpstreamEvidenceProvider:
    def __init__(
        self,
        *,
        adapter: DatadogCorrelationAdapter,
        queries: DatadogCorrelationQueries | None = None,
        target_resource: str = "unknown-rds",
    ) -> None:
        self._adapter = adapter
        self._queries = queries or DatadogCorrelationQueries()
        self._target_resource = target_resource or "unknown-rds"

    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        _ = alert_id

        rds_cpu_metric = _scoped_rds_metric(
            self._queries.rds_cpu_metric,
            target_resource=self._target_resource,
            scope_tag=self._queries.rds_scope_tag,
        )

        rds_connections_metric = _scoped_rds_metric(
            self._queries.rds_connections_metric,
            target_resource=self._target_resource,
            scope_tag=self._queries.rds_scope_tag,
        )

        rds_metrics = (
            self._adapter.query_metric_series(
                metric_name=rds_cpu_metric,
                start=window_start,
                end=window_end,
            ),
            self._adapter.query_metric_series(
                metric_name=rds_connections_metric,
                start=window_start,
                end=window_end,
            ),
        )

        upstream_service_names = self._queries.upstream_service_names or (service_name,)
        upstream_metric_names = tuple(
            self._queries.upstream_cpu_metric_template % upstream_service
            for upstream_service in upstream_service_names
            if upstream_service
        )

        upstream_metrics = tuple(
            self._adapter.query_metric_series(
                metric_name=upstream_metric_name,
                start=window_start,
                end=window_end,
            )
            for upstream_metric_name in upstream_metric_names
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

        topology_hints = tuple(
            TopologyHint(
                source=upstream_metric_name,
                target=self._target_resource,
                relation="upstream_of",
            )
            for upstream_metric_name in upstream_metric_names
        )

        return UpstreamEvidenceBundle(
            rds_metrics=rds_metrics,
            upstream_metrics=upstream_metrics,
            web_request_logs=web_request_logs,
            app_logs=app_logs,
            topology_hints=topology_hints,
            operator_hints=(),
        )
