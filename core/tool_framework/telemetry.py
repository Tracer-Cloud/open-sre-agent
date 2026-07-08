"""Shared error-reporting helpers for tool call sites.

``report_run_error`` is for tools that deliberately swallow exceptions and
return a degraded ``{"available": False, ...}`` dict. It turns a silent
swallow into a structured log entry plus Sentry event.

``invoke_tool`` is the unified dispatch wrapper used by ``BaseTool.__call__``
and ``RegisteredTool.__call__``. It owns the single try/except + error-capture
contract so both call paths behave identically.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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


def invoke_tool(
    run_fn: Callable[..., Any],
    *,
    name: str,
    source: str,
    kwargs: dict[str, Any],
) -> Any:
    """Call ``run_fn(**kwargs)`` and capture any exception via ``report_exception``.

    Returns the run result on success, or
    ``{"error": ..., "exception_type": ...}`` on failure — the shape both
    ``BaseTool.__call__`` and ``RegisteredTool.__call__`` have always returned.
    """
    try:
        return run_fn(**kwargs)
    except Exception as exc:
        report_exception(
            exc,
            logger=_DEFAULT_LOGGER,
            message=f"Tool {name} failed: {type(exc).__name__}: {exc}",
            severity="error",
            tags={"surface": "tool", "tool_name": name, "source": source},
        )
        return {"error": str(exc), "exception_type": type(exc).__name__}


__all__ = ["ToolErrorSeverity", "invoke_tool", "report_run_error"]
