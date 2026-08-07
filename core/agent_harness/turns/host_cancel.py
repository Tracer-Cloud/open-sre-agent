"""Detect cooperative host cancel (gateway ``sink.turn_cancel`` Event).

Shell cancel still flows through ``console.cancel_requested`` on the action /
gather tool context. Chat hosts attach a ``threading.Event`` on the turn sink;
``LiveOutputSink`` forwards it so the orchestrator can skip gather/answer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


def host_cancel_requested(output: Any | None) -> bool:
    """True when the bound sink's ``turn_cancel`` Event is set."""
    if output is None:
        return False
    cancel = getattr(output, "turn_cancel", None)
    return isinstance(cancel, threading.Event) and cancel.is_set()


def cancel_probe_console(is_cancelled: Callable[[], bool]) -> Any:
    """Minimal console stand-in for gather ReAct ``cancel_requested`` checks."""

    class _ProbeConsole:
        @property
        def cancel_requested(self) -> bool:
            return bool(is_cancelled())

        def print(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

    return _ProbeConsole()


def cancel_tool_resources(is_cancelled: Callable[[], bool] | None) -> dict[str, Any]:
    """``tool_resources`` so :class:`~core.agent.react_loop.ReactLoop` sees cancel."""
    if is_cancelled is None:
        return {}
    from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY

    # Bind the narrowed callable: inside the nested class body the parameter
    # widens back to ``| None``.
    probe = is_cancelled

    class _ProbeContext:
        def __init__(self) -> None:
            self.console = cancel_probe_console(probe)

    return {ACTION_TOOL_CONTEXT_RESOURCE_KEY: _ProbeContext()}


__all__ = [
    "cancel_probe_console",
    "cancel_tool_resources",
    "host_cancel_requested",
]
