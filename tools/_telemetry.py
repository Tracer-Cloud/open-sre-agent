"""Shared error-reporting helper for HTTP/cloud tool wrappers.

Many tools under ``tools/`` deliberately catch broad exceptions and return
a degraded ``{"available": False, "error": ..., <empty payload>}`` dict so the
LLM planner can see the failure rather than crashing the graph. Without an
explicit Sentry capture at those sites, the agent's experience of "available =
False" is invisible to operators — the global tool wrapper (#1476) cannot help
because the exception never escapes ``run()``.

``report_run_error`` is the single place that turns one of these silent
swallow sites into a structured log entry plus a Sentry event tagged with
``surface=tool``, ``tool_name``, ``source``, and ``component``. Severity
defaults to ``error``; pass ``severity="warning"`` for known-recoverable
states (e.g. HTTP 4xx, "integration not configured") that are still worth
tracking because they shape what the agent sees.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from platform.observability.errors import report_exception

ToolErrorSeverity = Literal["error", "warning"]

_DEFAULT_LOGGER = logging.getLogger("tools")


def report_run_error(
    exc: BaseException,
    *,
    tool_name: str,
    source: str,
    component: str,
    method: str | None = None,
    severity: ToolErrorSeverity = "error",
    logger: logging.Logger | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Log + Sentry-capture an error swallowed by a tool wrapper.

    ``tool_name`` and ``source`` come from the tool's metadata (the
    ``name=``/``source=`` arguments of ``@tool`` or the corresponding
    ``BaseTool`` ClassVars). ``component`` should identify the call site —
    typically ``"<module>.<function_or_class>"`` — so Sentry groups events
    per tool implementation, not per top-level surface tag.
    """
    tags: dict[str, str] = {
        "surface": "tool",
        "tool_name": tool_name,
        "source": source,
        "component": component,
    }
    if method:
        tags["method"] = method
    report_exception(
        exc,
        logger=logger or _DEFAULT_LOGGER,
        message=f"Tool {tool_name} failed: {type(exc).__name__}: {exc}",
        severity=severity,
        tags=tags,
        extras=extras,
    )


__all__ = ["ToolErrorSeverity", "report_run_error"]
