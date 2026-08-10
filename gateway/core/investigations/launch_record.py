"""Detached investigation launch record and context binding.

One record per turn, thread-safe, no I/O. Tracks which detached investigations
were accepted during one chat turn so the surfaces that signal "done" know
whether to mark the turn as complete or leave it in a pending state.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class DetachedLaunchRecord:
    """Ledger of detached investigations accepted during one chat turn.

    A turn that queued work is not finished when the turn ends, so the surfaces
    that signal "done" have to know. This is the only signal that crosses from
    the launch call back out to the dispatcher.
    """

    def __init__(self) -> None:
        self._investigation_ids: list[str] = []
        self._accepted_call_ids: set[str] = set()
        self._lock = threading.Lock()

    def note_accepted(self, investigation_id: str, *, call_id: str | None = None) -> None:
        """Record that a detached investigation was accepted.

        ``call_id`` is the id of the tool call that triggered *this specific*
        launch, when known. It is what lets a caller later ask "did this exact
        call detach" instead of only "did some call in the turn detach" — a
        turn's batch can mix a detaching call with an ordinary one that must
        still close normally.
        """
        with self._lock:
            self._investigation_ids.append(investigation_id)
            if call_id:
                self._accepted_call_ids.add(call_id)

    @property
    def any_accepted(self) -> bool:
        """True if any detached investigations were accepted during this turn."""
        with self._lock:
            return len(self._investigation_ids) > 0

    @property
    def investigation_ids(self) -> tuple[str, ...]:
        """All investigation IDs accepted during this turn."""
        with self._lock:
            return tuple(self._investigation_ids)

    def call_detached(self, call_id: str | None) -> bool:
        """True if ``call_id`` is the specific tool call that triggered a detach.

        ``None``/blank never matches: a caller with no id to check (e.g. a
        synthetic event) must not be treated as the launching call just
        because *some* call in the turn detached.
        """
        if not call_id:
            return False
        with self._lock:
            return call_id in self._accepted_call_ids


_CURRENT_DETACHED_LAUNCH_RECORD: ContextVar[DetachedLaunchRecord | None] = ContextVar(
    "_CURRENT_DETACHED_LAUNCH_RECORD", default=None
)


@contextmanager
def bound_detached_launch_record(record: DetachedLaunchRecord) -> Iterator[None]:
    """Bind a detached launch record for the current context."""
    token = _CURRENT_DETACHED_LAUNCH_RECORD.set(record)
    try:
        yield
    finally:
        _CURRENT_DETACHED_LAUNCH_RECORD.reset(token)


def current_detached_launch_record() -> DetachedLaunchRecord | None:
    """Return the bound detached launch record, or None when unbound."""
    return _CURRENT_DETACHED_LAUNCH_RECORD.get()
