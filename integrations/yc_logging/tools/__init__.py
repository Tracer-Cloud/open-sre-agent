"""Agent-callable tools for Cloud Logging."""

from __future__ import annotations

from integrations.yc_logging.tools.yc_logs_tool import list_yc_log_groups, read_yc_logs

__all__ = ["list_yc_log_groups", "read_yc_logs"]
