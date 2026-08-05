"""Cross-process signal for live scheduler hosts to resync jobs from the store.

The interactive shell and CLI mutate ``scheduler_tasks.json``; the long-lived
gateway scheduler only sees those mutations when it reloads. Writers touch a
file under ``~/.opensre``; the gateway polls and resyncs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

_RELOAD_FILENAME = "scheduler_reload_requested"
# Gateway poll interval for the reload signal file.
RELOAD_POLL_SECONDS = 2.0


def _signal_path() -> Path:
    return OPENSRE_HOME_DIR / _RELOAD_FILENAME


def request_scheduler_reload() -> None:
    """Ask any live scheduler host to resync jobs from the task store."""
    path = _signal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


def consume_scheduler_reload_request() -> bool:
    """Return True once if a reload was requested, clearing the signal."""
    path = _signal_path()
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


__all__ = [
    "RELOAD_POLL_SECONDS",
    "consume_scheduler_reload_request",
    "request_scheduler_reload",
]
