"""File-write conflict detection for ``/fleet conflicts``.

Pure detection logic over write events. Presentation lives in
``interactive_shell.command_registry.agents.conflicts_view`` so this
module stays independent of the interactive shell.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

#: Default conflict window. Two distinct agents writing the same file within
#: this many seconds is treated as a conflict (per AniketXD on Discord).
DEFAULT_WINDOW_SECONDS: float = 10.0


@dataclass(frozen=True)
class WriteEvent:
    """A single observed write to a path by an agent process.

    ``agent`` is a display name plus pid (e.g. ``"claude-code:8421"``).
    ``timestamp`` is unix seconds, when the write was observed.
    """

    agent: str
    path: str
    timestamp: float


@dataclass(frozen=True)
class FileWriteConflict:
    """Two or more distinct agents wrote to the same path within the window.

    ``agents`` is sorted alphabetically for stable output. ``first_seen`` and
    ``last_seen`` are the earliest and latest observed write timestamps in the
    colliding cluster.
    """

    path: str
    agents: tuple[str, ...]
    first_seen: float
    last_seen: float


def detect_conflicts(
    events: Sequence[WriteEvent],
    window_seconds: float,
    opensre_agent_id: str,
) -> list[FileWriteConflict]:
    """Return file-write conflicts within ``window_seconds`` of the most recent event.

    A conflict is a path written by two or more distinct agents whose write
    events all fall within ``window_seconds`` of the most recent non-OpenSRE
    event. Repeated writes by the same agent collapse to a single entry in
    ``agents``. Events whose ``agent`` matches ``opensre_agent_id`` are removed
    before window selection so OpenSRE never reports itself as a colliding
    agent and never anchors the window with its own activity.

    The window is anchored on the most recent event timestamp rather than
    wall-clock ``now`` so the function stays pure and testable. The boundary is
    inclusive: an event exactly ``window_seconds`` older than the anchor is kept.
    Results are sorted by ``last_seen`` descending (freshest collisions first),
    with ``path`` ascending as a stable tiebreaker.
    """
    relevant = [e for e in events if e.agent != opensre_agent_id]
    if not relevant:
        return []

    anchor = max(e.timestamp for e in relevant)
    in_window = [e for e in relevant if anchor - e.timestamp <= window_seconds]

    by_path: dict[str, list[WriteEvent]] = defaultdict(list)
    for event in in_window:
        by_path[event.path].append(event)

    conflicts: list[FileWriteConflict] = []
    for path, group in by_path.items():
        distinct_agents = {e.agent for e in group}
        if len(distinct_agents) < 2:
            continue
        timestamps = [e.timestamp for e in group]
        conflicts.append(
            FileWriteConflict(
                path=path,
                agents=tuple(sorted(distinct_agents)),
                first_seen=min(timestamps),
                last_seen=max(timestamps),
            )
        )

    conflicts.sort(key=lambda c: c.path)
    conflicts.sort(key=lambda c: c.last_seen, reverse=True)
    return conflicts


__all__ = [
    "DEFAULT_WINDOW_SECONDS",
    "FileWriteConflict",
    "WriteEvent",
    "detect_conflicts",
]
