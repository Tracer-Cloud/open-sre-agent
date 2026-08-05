"""Structured offers awaiting a bare affirmative (yes / sure / …).

Schedule confirmation must not scrape Want-me-to prose. The turn that proposes
the schedule writes a :class:`PendingScheduleOffer` onto the session; ``yes``
reads that object and becomes a literal ``/cron add …`` with no regex.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

# Common morning-report defaults → human cadence labels (exact cron match only).
_CADENCE_LABELS: dict[str, str] = {
    "0 8 * * 1-5": "every weekday at 8am",
    "0 9 * * 1-5": "every weekday at 9am",
    "0 7 * * 1-5": "every weekday at 7am",
}


@dataclass(frozen=True, slots=True)
class PendingScheduleOffer:
    """A schedule the user has been offered and has not yet confirmed."""

    kind: str
    cron: str
    timezone: str
    provider: str
    chat_id: str = ""

    def to_slash_command(self) -> str:
        """Literal slash the action driver dispatches without an LLM round-trip."""
        args = [
            "add",
            "--kind",
            self.kind,
            "--cron",
            self.cron,
            "--tz",
            self.timezone,
            "--provider",
            self.provider,
        ]
        chat = self.chat_id.strip()
        if chat:
            args.extend(["--chat-id", chat])
        # shlex.quote the parts: the five-field cron expression is one argument,
        # and the dispatcher tokenises this text before the CLI ever sees it.
        return "/cron " + " ".join(shlex.quote(arg) for arg in args)

    def want_me_to_body(self) -> str:
        """Canonical closer body (no leading Want me to:) for the assistant to show."""
        cadence = _CADENCE_LABELS.get(self.cron.strip(), f"on cron {self.cron}")
        dest = self.provider
        chat = self.chat_id.strip()
        if chat:
            dest = f"{self.provider} ({chat})"
        return f"schedule this as a recurring {self.kind} {cadence} to {dest}"


__all__ = ["PendingScheduleOffer"]
