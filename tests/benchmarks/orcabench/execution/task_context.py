"""Translate public ORCA instruction context into generic investigation inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


_CURRENT_TIME_RE = re.compile(
    r"\bThe current time is\s+"
    r"(?P<month>[A-Z][a-z]{2})\s+"
    r"(?P<day>\d{1,2}),\s+"
    r"(?P<year>\d{4})\s+at\s+"
    r"(?P<clock>\d{1,2}:\d{2})(?:\s*(?P<ampm>[AP]M))?\s+ET\.",
)


@dataclass(frozen=True)
class OrcaTaskContext:
    """Agent-visible temporal context extracted from an ORCA instruction."""

    current_time: datetime

    def incident_window(self, *, lookback_minutes: int = 120) -> dict[str, object]:
        """Return OpenSRE's generic serialized historical investigation window."""
        until = self.current_time.astimezone(UTC)
        since = until - timedelta(minutes=lookback_minutes)
        return {
            "_schema_version": 1,
            "since": since.isoformat().replace("+00:00", "Z"),
            "until": until.isoformat().replace("+00:00", "Z"),
            "source": "caller_override",
            "confidence": 1.0,
        }


def parse_orca_task_context(instruction: str) -> OrcaTaskContext:
    """Parse ORCA's standardized simulated current-time sentence.

    The parser intentionally rejects absent or ambiguous context instead of silently
    falling back to the host clock, which would query the wrong historical snapshot.
    """
    match = _CURRENT_TIME_RE.search(instruction)
    if match is None:
        raise ValueError("ORCA instruction is missing its standardized current time")

    clock = match.group("clock")
    ampm = match.group("ampm")
    time_format = "%b %d, %Y %I:%M %p" if ampm else "%b %d, %Y %H:%M"
    rendered = (
        f"{match.group('month')} {match.group('day')}, {match.group('year')} "
        f"{clock}{f' {ampm}' if ampm else ''}"
    )
    local = datetime.strptime(rendered, time_format).replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    return OrcaTaskContext(current_time=local)
