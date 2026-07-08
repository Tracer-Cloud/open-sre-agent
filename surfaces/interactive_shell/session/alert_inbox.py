"""The session's inbox of externally-received alerts.

A surface facet composed onto :class:`~surfaces.interactive_shell.session.session.Session`:
the interactive shell's alert listener appends externally-received alerts here, and
``/status`` reads them. Kept out of the core session so consumers that never touch
alerts don't see the field.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.alerts.inbox import IncomingAlert

# Bounded buffer so the alert listener can't grow memory unbounded — keeps the last
# few hundred alerts for /status. A round default (not tuned); preserved from the
# original ``_INCOMING_ALERTS_MAX``.
_DEFAULT_MAX = 256


@dataclass
class SessionAlertInbox:
    """Bounded FIFO of received alerts shown in ``/status`` (oldest dropped past the cap)."""

    entries: list[IncomingAlert] = field(default_factory=list)
    _max: int = _DEFAULT_MAX

    def add(self, alert: IncomingAlert) -> None:
        """Append an alert, dropping the oldest once the cap is exceeded."""
        self.entries.append(alert)
        if len(self.entries) > self._max:
            del self.entries[0]

    @property
    def most_recent(self) -> IncomingAlert | None:
        """The newest alert, or None when the inbox is empty."""
        return self.entries[-1] if self.entries else None

    def clear(self) -> None:
        self.entries.clear()
