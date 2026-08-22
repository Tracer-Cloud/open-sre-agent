"""Cross-process signal for live scheduler hosts to resync jobs from the store.

The interactive shell and CLI mutate the task store; the long-lived gateway
scheduler only sees those mutations when it reloads. Writers touch a file
under ``~/.opensre``; the gateway polls and resyncs. Do not add a second
scheduler inside ``gateway/``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

_RELOAD_FILENAME = "scheduler_reload_requested"
# Gateway poll interval for the reload signal file.
RELOAD_POLL_SECONDS = 2.0


def _signal_path() -> Path:
    return OPENSRE_HOME_DIR / _RELOAD_FILENAME


def request_scheduler_reload() -> None:
    """Ask any live scheduler host to resync jobs from the task store.

    Best-effort: a failure to write the signal is logged, never raised, so it
    cannot fail the store mutation that triggered it — the scheduler still
    resyncs on its next poll or on restart.
    """
    path = _signal_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError:
        logger.warning("Could not write scheduler reload signal at %s", path, exc_info=True)


def consume_scheduler_reload_request() -> bool:
    """Return True once if a reload was requested, clearing the signal.

    Atomic: the unlink both tests for and clears the request, so two polls
    cannot both observe the same one.
    """
    try:
        _signal_path().unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not clear scheduler reload signal", exc_info=True)
        return False
    return True


__all__ = [
    "RELOAD_POLL_SECONDS",
    "consume_scheduler_reload_request",
    "request_scheduler_reload",
]
