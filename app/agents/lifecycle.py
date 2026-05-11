"""Agent process lifecycle management.

Provides ``terminate()`` — a SIGTERM-then-SIGKILL helper used by the
``/agents kill`` slash command to stop a runaway local AI agent from
within the opensre interactive shell.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ``os.kill(pid, 0)`` on Windows raises ``OSError`` (WinError 87) for invalid PIDs
# instead of ``ProcessLookupError``.
_WIN_ERR_INVALID_PARAMETER = 87

# Default grace period between SIGTERM and SIGKILL escalation.
DEFAULT_GRACE_SECONDS: float = 5.0

# Polling interval while waiting for the process to exit after SIGTERM.
_POLL_INTERVAL: float = 0.1


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* still exists (works on Unix and macOS)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack permission — treat as alive.
        return True
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == _WIN_ERR_INVALID_PARAMETER:
            return False
        raise


def _assert_target_pid_exists(pid: int) -> None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == _WIN_ERR_INVALID_PARAMETER:
            raise ProcessLookupError(pid) from exc
        raise


@dataclass(frozen=True)
class TerminateResult:
    """Outcome of a :func:`terminate` call."""

    pid: int
    exited: bool
    signal_sent: str  # "SIGTERM" | "SIGKILL"
    elapsed_seconds: float


def terminate(pid: int, *, grace_s: float = DEFAULT_GRACE_SECONDS) -> TerminateResult:
    """Send SIGTERM, wait up to *grace_s* seconds, escalate to SIGKILL.

    Returns a :class:`TerminateResult` describing what happened.
    Raises ``ProcessLookupError`` if *pid* does not exist at call time.
    Raises ``PermissionError`` if the calling user cannot signal *pid*.
    """
    # Validate that the process exists before proceeding.
    _assert_target_pid_exists(pid)

    t0 = time.monotonic()

    # --- SIGTERM ---
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Raced: process exited between the check and the signal.
        return TerminateResult(
            pid=pid,
            exited=True,
            signal_sent="SIGTERM",
            elapsed_seconds=time.monotonic() - t0,
        )

    logger.info("Sent SIGTERM to pid %d, waiting up to %.1fs", pid, grace_s)

    deadline = t0 + grace_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return TerminateResult(
                pid=pid,
                exited=True,
                signal_sent="SIGTERM",
                elapsed_seconds=time.monotonic() - t0,
            )
        time.sleep(_POLL_INTERVAL)

    # --- SIGKILL escalation (or second SIGTERM on platforms without SIGKILL, e.g. Windows)
    logger.warning("pid %d did not exit after SIGTERM; sending SIGKILL", pid)
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, kill_signal)
    except ProcessLookupError:
        return TerminateResult(
            pid=pid,
            exited=True,
            signal_sent="SIGTERM",
            elapsed_seconds=time.monotonic() - t0,
        )

    # Brief wait for SIGKILL to take effect.
    kill_deadline = time.monotonic() + 1.0
    while time.monotonic() < kill_deadline:
        if not _pid_alive(pid):
            return TerminateResult(
                pid=pid,
                exited=True,
                signal_sent="SIGKILL",
                elapsed_seconds=time.monotonic() - t0,
            )
        time.sleep(_POLL_INTERVAL)
    return TerminateResult(
        pid=pid,
        exited=not _pid_alive(pid),
        signal_sent="SIGKILL",
        elapsed_seconds=time.monotonic() - t0,
    )
