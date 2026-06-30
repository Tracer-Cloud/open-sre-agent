"""Backward-compatible re-exports — prefer ``platform.common.log_compaction``."""

from __future__ import annotations

from platform.common.log_compaction import (
    _classify_error_type,
    _extract_components,
    _normalize_message,
    build_error_taxonomy,
    compact_logs,
    deduplicate_logs,
)

__all__ = [
    "_classify_error_type",
    "_extract_components",
    "_normalize_message",
    "build_error_taxonomy",
    "compact_logs",
    "deduplicate_logs",
]
