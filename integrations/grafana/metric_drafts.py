"""Grafana owns PromQL draft fences for unformed metric answers.

Core's metric floor decides *when* to append a draft; this package supplies
the Grafana dialect.
"""

from __future__ import annotations

from platform.harness_ports import register_metric_query_draft

_DRAFT_PROMQL = """```promql
-- Draft PromQL: confirm metric name and window, then run in Grafana.
-- This is not a live reading.
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1h])) by (le))
```"""


def register_grafana_metric_drafts() -> None:
    """Opt Grafana into the unformed-metric draft floor."""
    register_metric_query_draft("grafana", count_draft=_DRAFT_PROMQL)


__all__ = ["register_grafana_metric_drafts"]
