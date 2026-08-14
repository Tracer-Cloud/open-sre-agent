"""Terminal-outcome arbitration and soft deadlines for gateway turns."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class TerminalOutcomeArbiter:
    """Let only the first terminal path update a turn's external state."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._claimed = False

    def claim(self) -> bool:
        """Claim the terminal outcome, returning whether this caller won."""
        with self._lock:
            if self._claimed:
                return False
            self._claimed = True
            return True

    @contextmanager
    def timeout_after(
        self,
        timeout_seconds: float,
        on_timeout: Callable[[], None],
    ) -> Iterator[None]:
        """Arm a cooperative timeout for the duration of the context."""

        def _handle_timeout() -> None:
            self.cancel_event.set()
            if self.claim():
                on_timeout()

        timer = threading.Timer(timeout_seconds, _handle_timeout)
        timer.start()
        try:
            yield
        finally:
            timer.cancel()


__all__ = ["TerminalOutcomeArbiter"]
