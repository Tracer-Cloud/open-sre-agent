"""Bounded RCA summary sections for chat-notification channels.

Chat platforms cap a message at 4096 characters and the transports
tail-truncate to fit. The RCA body ends with "What to do next" and the stats
block, so an unbounded root cause would push exactly the actionable sections
off the end. Budget each section instead: the worst case below stays under
the cap, so the tail always survives.

Email keeps the full report; chat channels carry this bounded summary and
point the reader at ``/background show`` for the rest.
"""

from __future__ import annotations

from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)

_COMMAND_CHARS = 200
_ROOT_CAUSE_CHARS = 1000
_ITEM_CHARS = 240
_MAX_ITEMS = 5


def summary_sections(
    record: BackgroundInvestigationRecord,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Return the RCA sections trimmed to fit one chat message."""
    from platform.common.truncation import truncate

    def _items(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(truncate(value, _ITEM_CHARS, suffix="…") for value in values[:_MAX_ITEMS])

    return (
        truncate(record.command, _COMMAND_CHARS, suffix="…"),
        truncate(record.root_cause, _ROOT_CAUSE_CHARS, suffix="…"),
        _items(record.top_analysis),
        _items(record.next_steps),
    )
