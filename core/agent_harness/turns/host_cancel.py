"""Host cancel signal for chat turns (gateway ``sink.turn_cancel``).

Mental model (one Event, three readers)::

    transport timeout / ``/stop``
            │
            ▼
    sink.turn_cancel  ──►  CancelConsole.cancel_requested  ──►  ReAct + tools
            │
            ├──────────►  host_cancel_requested(output)   ──►  orchestrator
            └──────────►  LiveOutputSink stream guard

Shell cancel stays on ``StreamingConsole``; it never needs ``turn_cancel``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY


def host_cancel_requested(output: Any | None) -> bool:
    """True when the bound sink's ``turn_cancel`` Event is set."""
    if output is None:
        return False
    cancel = getattr(output, "turn_cancel", None)
    return isinstance(cancel, threading.Event) and cancel.is_set()


@dataclass(frozen=True, slots=True)
class CancelProbeConsole:
    """Minimal console so gather ReAct sees the same ``cancel_requested`` flag."""

    is_cancelled: Callable[[], bool]

    @property
    def cancel_requested(self) -> bool:
        return bool(self.is_cancelled())

    def print(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)


@dataclass(frozen=True, slots=True)
class CancelProbeContext:
    """Stand-in ``ActionToolContext`` carrying only the cancel console."""

    console: CancelProbeConsole


def cancel_tool_resources(is_cancelled: Callable[[], bool] | None) -> dict[str, Any]:
    """``tool_resources`` so :class:`~core.agent.react_loop.ReactLoop` sees cancel."""
    if is_cancelled is None:
        return {}
    return {
        ACTION_TOOL_CONTEXT_RESOURCE_KEY: CancelProbeContext(
            console=CancelProbeConsole(is_cancelled=is_cancelled)
        )
    }


__all__ = [
    "CancelProbeConsole",
    "CancelProbeContext",
    "cancel_tool_resources",
    "host_cancel_requested",
]
