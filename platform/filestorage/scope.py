"""What may leave the laptop.

Only conversation history and memory sync. Credentials stay on the machine:
``integrations.json`` holds live provider secrets, ``llm-auth.json`` holds model
keys, and neither is needed to resume a conversation elsewhere — a second
machine re-runs the integration wizard.

The rule is expressed as an allowlist of roots rather than a deny-list of
filenames, so a file added to ``~/.opensre`` later is excluded by default
instead of silently uploaded. The denied names below are a second, redundant
check on top of that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import get_memory_dir
from core.agent_harness.session.persistence.paths import sessions_dir

# Never uploaded, whatever else changes. Redundant with the allowlist of roots
# and kept that way: two independent checks, so one mistake is not enough.
DENIED_FILENAMES = frozenset(
    {
        "integrations.json",
        "llm-auth.json",
        "opensre.json",
        "config.yml",
        "anonymous_id",
    }
)


@dataclass(frozen=True)
class SyncRoot:
    """One local directory and the key prefix it maps to in the bucket."""

    name: str
    path: Path


def syncable_roots() -> tuple[SyncRoot, ...]:
    """Directories that mirror to the bucket, resolved for the current scope."""
    return (
        SyncRoot(name="sessions", path=sessions_dir()),
        SyncRoot(name="memory", path=get_memory_dir()),
    )


def is_syncable(path: Path, *, roots: tuple[SyncRoot, ...] | None = None) -> bool:
    """Whether ``path`` is inside a synced root and not a denied file."""
    if path.name in DENIED_FILENAMES:
        return False
    candidate = path.resolve()
    for root in roots if roots is not None else syncable_roots():
        resolved_root = root.path.resolve()
        if candidate == resolved_root or resolved_root in candidate.parents:
            return True
    return False


__all__ = ["DENIED_FILENAMES", "SyncRoot", "is_syncable", "syncable_roots"]
