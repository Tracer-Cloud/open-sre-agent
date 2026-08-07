"""Thread-safe map from a posted approval-prompt event id to its approval id.

Written by :class:`gateway.transports.buzz.approvals.BuzzApprovalPrompter` on
the turn's executor thread; read by the poll loop
(:mod:`gateway.transports.buzz.background`) on the asyncio loop thread — the
lock is what makes that safe, mirroring
:class:`gateway.core.runtime.approvals.ApprovalBroker`'s own locking.
"""

from __future__ import annotations

import threading


class PendingApprovals:
    """Maps a posted approval-prompt event id -> ``ApprovalBroker`` approval id."""

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, event_id: str, approval_id: str) -> None:
        with self._lock:
            self._pending[event_id] = approval_id

    def discard(self, event_id: str) -> None:
        with self._lock:
            self._pending.pop(event_id, None)

    def peek_match(self, reply_event_ids: frozenset[str]) -> str | None:
        """Return the approval id one of *reply_event_ids* resolves, without popping it.

        Used to check authorization before committing to resolve — popping
        first would let an unauthorized reply consume the slot and lock out
        the real, authorized responder.
        """
        with self._lock:
            for target in reply_event_ids:
                approval_id = self._pending.get(target)
                if approval_id is not None:
                    return approval_id
        return None

    def pop_match(self, reply_event_ids: frozenset[str]) -> str | None:
        """Pop and return the approval id one of *reply_event_ids* resolves, if any."""
        with self._lock:
            for target in reply_event_ids:
                approval_id = self._pending.pop(target, None)
                if approval_id is not None:
                    return approval_id
        return None


__all__ = ["PendingApprovals"]
