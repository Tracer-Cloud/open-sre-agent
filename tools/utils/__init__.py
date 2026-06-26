"""Tool utilities - code-host helpers, data validation, evidence compaction, and log deduplication."""

from tools.utils.code_host_unavailable import code_host_unavailable_payload
from tools.utils.compaction import (
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
from tools.utils.data_validation import validate_host_metrics
from tools.utils.db_warnings import default_db_warning
from tools.utils.log_compaction import (
    build_error_taxonomy,
    deduplicate_logs,
)

__all__ = [
    # Database warnings
    "default_db_warning",
    # Code-host utilities
    "code_host_unavailable_payload",
    # Data validation
    "validate_host_metrics",
    # Compaction utilities
    "compact_logs",
    "compact_traces",
    "compact_metrics",
    "compact_invocations",
    "summarize_counts",
    "truncate_list",
    "truncate_message",
    "truncate_log_entry",
    # Log deduplication and taxonomy
    "deduplicate_logs",
    "build_error_taxonomy",
    # Constants
    "DEFAULT_LOG_LIMIT",
    "DEFAULT_ERROR_LOG_LIMIT",
    "DEFAULT_TRACE_LIMIT",
    "DEFAULT_METRICS_LIMIT",
    "DEFAULT_MESSAGE_CHARS",
]
