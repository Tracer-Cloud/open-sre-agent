"""Cooperative cancel detection for the ReAct loop (host-agnostic).

Hosts put a console with ``cancel_requested`` on an object in
``tool_resources``:

* shell — ``StreamingConsole``
* gateway — ``CancelConsole`` (wraps ``sink.turn_cancel``)
* gather — cancel probe from ``host_cancel.cancel_tool_resources``

Tools already short-circuit on the same flag; the loop uses this helper so the
next LLM iteration does not start after cancel is set.
"""

from __future__ import annotations

from typing import Any


def tool_resources_cancel_requested(tool_resources: Any) -> bool:
    """True when any tool-resource console reports ``cancel_requested``."""
    if not isinstance(tool_resources, dict):
        return False
    for value in tool_resources.values():
        console = getattr(value, "console", None)
        if console is not None and bool(getattr(console, "cancel_requested", False)):
            return True
    return False


__all__ = ["tool_resources_cancel_requested"]
