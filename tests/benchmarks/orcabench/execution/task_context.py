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

_MARKDOWN_SECTION_RE = re.compile(
    r"^##\s+(?P<title>[^\n]+)\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
_REPORTED_ISSUE_RE = re.compile(
    r"^The following issue was reported[^\n]*:[ \t]*$\n"
    r"(?P<issue>.*?)(?=^NOTE:[ \t]*$)",
    re.MULTILINE | re.DOTALL,
)
_NOTE_BULLET_RE = re.compile(
    r"^\*\s+(?P<body>.*?)(?=^\*\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class OrcaTaskContext:
    """Agent-visible temporal context extracted from an ORCA instruction."""

    current_time: datetime
    reported_issue: str
    investigation_guidance: str

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

    def investigation_alert(self) -> dict[str, object]:
        """Return the ORCA report as a native OpenSRE dataset alert.

        Grafana is context available to the investigator, not the alert source.
        Keeping those concepts separate prevents benchmark instructions about
        Grafana from being mistaken for evidence that a Grafana alert fired.
        """
        return {
            "alert_source": "opensre_dataset",
            "_meta": {
                "orca_investigation_guidance": self.investigation_guidance,
            },
            "commonAnnotations": {
                "summary": self.reported_issue,
                "context_sources": "grafana,local_source",
            },
        }


def _markdown_section(instruction: str, title: str) -> str:
    for match in _MARKDOWN_SECTION_RE.finditer(instruction):
        if match.group("title").strip() == title:
            return match.group("body").strip()
    raise ValueError(f"ORCA instruction is missing its {title!r} section")


def _reported_issue(instruction: str) -> str:
    task_description = _markdown_section(instruction, "Task Description")
    match = _REPORTED_ISSUE_RE.search(task_description)
    if match is None:
        raise ValueError("ORCA task description is missing its reported issue")
    issue = match.group("issue").strip()
    if not issue:
        raise ValueError("ORCA task description contains an empty reported issue")
    return issue


def _investigation_guidance(instruction: str) -> str:
    task_description = _markdown_section(instruction, "Task Description")
    _, separator, note = task_description.partition("NOTE:")
    if not separator:
        raise ValueError("ORCA task description is missing its NOTE guidance")
    bullets = [
        " ".join(match.group("body").split())
        for match in _NOTE_BULLET_RE.finditer(note)
    ]
    if not bullets:
        raise ValueError("ORCA task description contains empty NOTE guidance")
    return "\n".join(bullets)


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
    return OrcaTaskContext(
        current_time=local,
        reported_issue=_reported_issue(instruction),
        investigation_guidance=_investigation_guidance(instruction),
    )
