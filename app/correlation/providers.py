from __future__ import annotations

from collections.abc import Callable

from app.correlation.upstream import (
    LogSignal,
    MetricSeries,
    TopologyHint,
    UpstreamEvidenceBundle,
)

MetricQuery = Callable[[str, str, str], tuple[MetricSeries, ...]]
LogQuery = Callable[[str, str, str], tuple[LogSignal, ...]]
TopologyQuery = Callable[[str], tuple[TopologyHint, ...]]
OperatorHintQuery = Callable[[str], tuple[str, ...]]


class NoopUpstreamEvidenceProvider:
    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        _ = (alert_id, service_name, window_start, window_end)
        return UpstreamEvidenceBundle()


class QueryBackedUpstreamEvidenceProvider:
    def __init__(
        self,
        *,
        rds_metric_query: MetricQuery,
        upstream_metric_query: MetricQuery,
        web_log_query: LogQuery,
        app_log_query: LogQuery,
        topology_query: TopologyQuery,
        operator_hint_query: OperatorHintQuery,
    ) -> None:
        self._rds_metric_query = rds_metric_query
        self._upstream_metric_query = upstream_metric_query
        self._web_log_query = web_log_query
        self._app_log_query = app_log_query
        self._topology_query = topology_query
        self._operator_hint_query = operator_hint_query

    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        _ = alert_id
        return UpstreamEvidenceBundle(
            rds_metrics=self._rds_metric_query(service_name, window_start, window_end),
            upstream_metrics=self._upstream_metric_query(service_name, window_start, window_end),
            web_request_logs=self._web_log_query(service_name, window_start, window_end),
            app_logs=self._app_log_query(service_name, window_start, window_end),
            topology_hints=self._topology_query(service_name),
            operator_hints=self._operator_hint_query(service_name),
        )
