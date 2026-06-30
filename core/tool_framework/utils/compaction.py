"""Backward-compatible re-exports — prefer ``platform.common.evidence_compaction``."""

from __future__ import annotations

from platform.common.evidence_compaction import (
    DEFAULT_ERROR_LOG_LIMIT,
    DEFAULT_LOG_LIMIT,
    DEFAULT_MESSAGE_CHARS,
    DEFAULT_METRICS_LIMIT,
    DEFAULT_TRACE_LIMIT,
    compact_invocations,
    compact_logs,
    compact_metrics,
    compact_traces,
    summarize_counts,
    truncate_list,
    truncate_log_entry,
    truncate_message,
)

__all__ = [
    "DEFAULT_ERROR_LOG_LIMIT",
    "DEFAULT_LOG_LIMIT",
    "DEFAULT_MESSAGE_CHARS",
    "DEFAULT_METRICS_LIMIT",
    "DEFAULT_TRACE_LIMIT",
    "compact_invocations",
    "compact_logs",
    "compact_metrics",
    "compact_traces",
    "summarize_counts",
    "truncate_list",
    "truncate_log_entry",
    "truncate_message",
]
