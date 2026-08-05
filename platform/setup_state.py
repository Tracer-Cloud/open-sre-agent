"""What the operator has connected and scheduled, as prompt-ready facts.

A leaf module: the assistant prompt renders this into its CONTEXT tier so the
agent reads the user's real setup instead of inferring it from conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
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

#: Look past a few in-flight rows so a pending newest run does not hide a
#: finished delivery that the model needs to see.
_FINISHED_RUN_LOOKBACK = 5


def _scheduled_tasks() -> list[Any]:
    from platform.scheduler.store import list_tasks

    return list(list_tasks())


def _latest_delivery_ok(tasks: Sequence[Any]) -> bool | None:
    """Whether the most recent finished run across ``tasks`` succeeded."""
    from platform.scheduler.claim_store import get_runs
    from platform.scheduler.types import TaskStatus

    finished = []
    for task in tasks:
        for run in get_runs(task.id, limit=_FINISHED_RUN_LOOKBACK):
            if run.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                finished.append(run)
                break
    if not finished:
        return None
    newest = max(finished, key=lambda run: run.started_at)
    return newest.status == TaskStatus.SUCCESS


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
