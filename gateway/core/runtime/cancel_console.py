"""Cooperative turn cancel for gateway hosts (shell ``StreamingConsole`` parity).

Transports attach a ``threading.Event`` as ``sink.turn_cancel`` and set it on
soft timeout (or later user stop). The turn handler wraps the pool console in
:class:`CancelConsole` so action tools and the ReAct loop see
``cancel_requested`` the same way the interactive shell does.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from rich.console import Console

# Set on the wrapper itself; everything else is proxied to the wrapped console.
_OWN_ATTRIBUTES = frozenset({"_output", "_cancel_event"})


class CancelConsole:
    """Console stand-in that exposes ``cancel_requested`` from a shared Event.

    Delegates rendering to the gateway pool's Rich console so tool observers and
    subprocess presenters keep working; only cancellation is added.

    Reads *and writes* are proxied. Slash capture turns recording on
    (``console.record = True``) and then calls ``export_text`` on the same
    object: if the write stopped at the wrapper, recording would stay off on
    the wrapped console and every slash command on a chat transport would fail.
    """

    def __init__(self, output: Console, cancel_event: threading.Event) -> None:
        self._output = output
        self._cancel_event = cancel_event

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._output.print(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._output, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _OWN_ATTRIBUTES:
            super().__setattr__(name, value)
            return
        setattr(self._output, name, value)


def ensure_turn_cancel(sink: Any) -> threading.Event:
    """Return the Event on ``sink.turn_cancel``, creating one when missing."""
    existing = getattr(sink, "turn_cancel", None)
    if isinstance(existing, threading.Event):
        return existing
    event = threading.Event()
    # Protocol sinks may reject dynamic attrs; the handler still holds
    # ``event`` for CancelConsole, and transports that own the concrete
    # sink set ``turn_cancel`` before calling the handler.
    with contextlib.suppress(Exception):
        sink.turn_cancel = event
    return event


__all__ = ["CancelConsole", "ensure_turn_cancel"]
