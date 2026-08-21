from __future__ import annotations

from infrastructure.harness_ports import register_metric_query_tools

_METRIC_QUERY_TOOLS = ("query_datadog_metrics",)


def register_datadog_metric_drafts() -> None:
    register_metric_query_tools("datadog", _METRIC_QUERY_TOOLS)


__all__ = ["register_datadog_metric_drafts"]
