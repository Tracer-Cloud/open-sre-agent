from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

_live_console: Console | None = None
_active_display: Any | None = None
_completed_footer_snapshot: tuple[str, float, str, str] | None = None
_tracker_toggle_stop_fn: Callable[[], None] | None = None
_investigation_spinner: Any | None = None
_investigation_plan_session: Any | None = None


def set_tracker_toggle_stop_fn(fn: Callable[[], None] | None) -> None:
    """Register callback used to stop tracker-owned keyboard watchers."""
    global _tracker_toggle_stop_fn
    _tracker_toggle_stop_fn = fn


def set_investigation_spinner(spinner: Any | None) -> None:
    """Register the prompt spinner the investigation display animates.

    ``/investigate`` dispatches as a literal slash command, so the turn-level
    "thinking" spinner never starts. Registering the active turn's spinner here
    lets ``_ReplEventLogDisplay`` drive it with per-stage phase labels
    (``set_phase``) and stop it (``stop``) as the pipeline runs.
    """
    global _investigation_spinner
    _investigation_spinner = spinner


def get_investigation_spinner() -> Any | None:
    return _investigation_spinner


def set_investigation_plan_session(session: Any | None) -> None:
    """Register the session whose ``task_plan`` tracks investigation stages.

    ProgressTracker / diagnose call :func:`advance_investigation_plan` while this
    pin is set. Cleared when the foreground run ends.
    """
    global _investigation_plan_session
    _investigation_plan_session = session


def get_investigation_plan_session() -> Any | None:
    """Return the session pinned for investigation plan advance, or ``None``."""
    return _investigation_plan_session


def advance_investigation_plan(node_name: str) -> None:
    """Advance the pinned session plan for a progress node. No-op if unset."""
    session = _investigation_plan_session
    if session is None:
        return
    plan = getattr(session, "task_plan", None)
    if plan is None:
        return
    from core.agent_harness.task_plan.investigation_progress import (
        advance_task_plan_for_investigation_node,
    )
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session
    from core.agent_harness.task_plan.work_log import record_task_plan_work
    from surfaces.shared.terminal.output.labels import _node_label

    updated = advance_task_plan_for_investigation_node(plan, node_name)
    if updated is not plan:
        apply_update_plan_session(session, updated, plan_only=False)
    # Attribute this stage to the step that is now in progress.
    record_task_plan_work(session, _node_label(node_name))


def complete_investigation_plan() -> None:
    """Mark the pinned session plan fully completed. No-op if unset."""
    session = _investigation_plan_session
    if session is None:
        return
    plan = getattr(session, "task_plan", None)
    if plan is None:
        return
    from core.agent_harness.task_plan.investigation_progress import complete_task_plan
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session

    updated = complete_task_plan(plan)
    if updated is plan:
        return
    apply_update_plan_session(session, updated, plan_only=False)


def _capture_footer_snapshot(display: Any) -> None:
    """Record the phase footer fields visible when a display stops."""
    global _completed_footer_snapshot
    if display is None:
        return
    t0 = getattr(display, "_t0", None)
    if t0 is None:
        return
    _completed_footer_snapshot = (
        getattr(display, "_current_phase", ""),
        time.monotonic() - t0,
        getattr(display, "_model", ""),
        getattr(display, "_mode", "local"),
    )


def consume_footer_snapshot() -> tuple[str, float, str, str] | None:
    global _completed_footer_snapshot
    snapshot, _completed_footer_snapshot = _completed_footer_snapshot, None
    return snapshot


def _get_console() -> Console:
    """Return the active Live console when running, else a fresh one."""
    return _live_console or Console(highlight=False)


def set_live_console(console: Console | None) -> None:
    global _live_console
    _live_console = console


def unregister_live_console(expected: Console | None) -> None:
    global _live_console
    if expected is not None and _live_console is expected:
        _live_console = None


def set_active_display(display: Any | None) -> None:
    global _active_display
    _active_display = display


def clear_active_display(expected: Any) -> None:
    global _active_display
    if _active_display is expected:
        _active_display = None


def stop_display() -> None:
    """Stop any running live display before printing final report output."""
    if _active_display is not None:
        _active_display.stop()

    if _tracker_toggle_stop_fn is not None:
        _tracker_toggle_stop_fn()
