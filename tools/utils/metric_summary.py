"""Backward-compatible re-exports — prefer ``platform.common.metric_summary``."""

from __future__ import annotations

from platform.common.metric_summary import summarize_prometheus_metrics

__all__ = ["summarize_prometheus_metrics"]
