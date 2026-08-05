"""What the operator has connected and scheduled, as prompt-ready facts.

A leaf module: the assistant prompt renders this into its CONTEXT tier so the
agent reads the user's real setup instead of inferring it from conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NOTHING_CONFIGURED = "none"
_NEVER_RUN = "never run"


@dataclass(frozen=True, slots=True)
class SetupSnapshot:
    """Frozen view of the user's setup at prompt-assembly time."""

    integrations: tuple[str, ...]
    schedule_count: int
    last_delivery_ok: bool | None


# Historical name — prefer :class:`SetupSnapshot`.
SetupState = SetupSnapshot


def _scheduled_tasks() -> list[Any]:
    from platform.scheduler.store import list_tasks

    return list(list_tasks())


def _store_paths() -> tuple[Path, ...]:
    """The files whose contents the snapshot is derived from.

    The run database runs in WAL mode, so a finished delivery lands in the
    ``-wal`` sidecar and the main file keeps its size and mtime until a
    checkpoint. Both are watched, or a completed run stays invisible.
    """
    from platform.scheduler.claim_store import _default_db_path
    from platform.scheduler.store import _default_store_path

    db = _default_db_path()
    return _default_store_path(), db, db.with_name(f"{db.name}-wal")


def setup_state_fingerprint() -> tuple[tuple[int, int], ...]:
    """A cheap change signal for the scheduler stores.

    A stat per store stands in for re-reading the task list and every task's
    run history, so a caller can cache the rendered block for a whole session
    and still notice a schedule added or a delivery completed mid-session.
    A missing store reads as zeroes — it appears once created.
    """
    marks: list[tuple[int, int]] = []
    for path in _store_paths():
        try:
            stat = path.stat()
        except OSError:
            marks.append((0, 0))
        else:
            marks.append((stat.st_mtime_ns, stat.st_size))
    return tuple(marks)


def _latest_finished_run(task_id: str) -> Any | None:
    from platform.scheduler.claim_store import get_latest_finished_run

    return get_latest_finished_run(task_id)


def _completed_at(run: Any) -> str:
    """When a run finished, falling back to its start for older rows."""
    return str(run.finished_at or run.started_at)


def _latest_delivery_ok(tasks: Sequence[Any]) -> bool | None:
    """Whether the delivery that finished most recently across ``tasks`` succeeded.

    Ordered by completion, not by start: runs overlap, so a slow task that
    started first can finish after a quick one. Picking by start time would
    report the quick run's outcome and hide the later failure. In-flight rows
    are ignored at the store layer so a burst of pending claims cannot hide
    the last completed delivery.
    """
    from platform.scheduler.types import TaskStatus

    finished = [run for task in tasks if (run := _latest_finished_run(task.id)) is not None]
    if not finished:
        return None
    newest = max(finished, key=_completed_at)
    return bool(newest.status == TaskStatus.SUCCESS)


def collect_setup_state(integrations: Sequence[str] = ()) -> SetupSnapshot:
    """Read live scheduler state and pair it with the caller's ``integrations``.

    The caller supplies the integration names because the session already holds
    the hydrated list; the harness port reports nothing until ports are
    installed, which would understate a configured install as empty.

    Returns an empty snapshot when the sources cannot be read: this feeds prompt
    assembly on every turn, so a fresh install without a scheduler store yet
    degrades to "nothing configured" rather than failing the turn.
    """
    try:
        tasks = _scheduled_tasks()
        return SetupSnapshot(
            integrations=tuple(integrations),
            schedule_count=len(tasks),
            last_delivery_ok=_latest_delivery_ok(tasks),
        )
    except Exception:
        logger.debug("setup state unavailable", exc_info=True)
        return SetupSnapshot(
            integrations=tuple(integrations), schedule_count=0, last_delivery_ok=None
        )


def _delivery_phrase(last_delivery_ok: bool | None) -> str:
    if last_delivery_ok is None:
        return _NEVER_RUN
    return "succeeded" if last_delivery_ok else "failed"


def render_setup_state(state: SetupSnapshot) -> str:
    """Render ``state`` as a fact block. States values only, never guidance."""
    integrations = ", ".join(state.integrations) or _NOTHING_CONFIGURED
    return (
        "--- Setup state ---\n"
        f"Integrations connected: {integrations}\n"
        f"Scheduled tasks configured: {state.schedule_count}\n"
        f"Last scheduled delivery: {_delivery_phrase(state.last_delivery_ok)}\n\n"
    )


__all__ = ["SetupSnapshot", "SetupState", "collect_setup_state", "render_setup_state"]
