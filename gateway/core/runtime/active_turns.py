"""In-flight turn cancel registry for gateway chat hosts.

Transports serialize turns per conversation, so a ``/stop`` message cannot
wait behind the same lock as the running turn. Instead each turn registers its
``turn_cancel`` Event here under a conversation key; ``/stop`` looks up the
key **outside** the turn lock, sets the Event, and optionally runs a
transport-supplied finalize callback (claim outcome + ``Stopped.`` copy).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_STOP_TOKENS = frozenset({"/stop", "stop", "/cancel", "cancel"})

USER_STOP_MESSAGE = "Stopped."


@dataclass(slots=True)
class _TrackedTurn:
    event: threading.Event
    on_user_stop: Callable[[], None] | None = None


class ActiveTurnCancels:
    """Thread-safe map of conversation key → in-flight cancel Event."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: dict[str, _TrackedTurn] = {}

    @contextmanager
    def track(
        self,
        key: str,
        event: threading.Event,
        *,
        on_user_stop: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        """Publish ``event`` for ``key`` for the duration of one turn."""
        with self._lock:
            self._turns[key] = _TrackedTurn(event=event, on_user_stop=on_user_stop)
        try:
            yield
        finally:
            with self._lock:
                current = self._turns.get(key)
                if current is not None and current.event is event:
                    del self._turns[key]

    def request_stop(self, key: str) -> bool:
        """Set the cancel Event for ``key``. Return whether a turn was active."""
        with self._lock:
            tracked = self._turns.get(key)
        if tracked is None:
            return False
        tracked.event.set()
        if tracked.on_user_stop is not None:
            tracked.on_user_stop()
        return True


def is_stop_command(text: str) -> bool:
    """True when the message is a user stop/cancel command (first token)."""
    stripped = text.strip()
    if not stripped:
        return False
    token = stripped.split(maxsplit=1)[0].lower()
    # Telegram: ``/stop@MyBot`` → ``/stop``
    if "@" in token:
        token = token.split("@", 1)[0]
    return token in _STOP_TOKENS


__all__ = [
    "USER_STOP_MESSAGE",
    "ActiveTurnCancels",
    "is_stop_command",
]
