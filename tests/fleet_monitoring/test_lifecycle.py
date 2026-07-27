"""Tests for agent lifecycle management (SIGTERM → SIGKILL)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

from tests.utils.polling import wait_until
from tools.system.fleet_monitoring.lifecycle import TerminateResult, terminate

# Windows ``os.kill`` / ``signal.SIGTERM`` delivery to a Python ``Popen`` child
# does not match POSIX (handlers may not run; escalation differs). These tests
# spawn children that rely on POSIX semantics and can hang or confuse ``-n auto``
# workers on ``windows-latest``.
_skip_win32_posix_signals = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX SIGTERM/SIGKILL child semantics are not reliable on Windows",
)


def _make_ready_path() -> str:
    """Reserve a path that does not yet exist, for a child to touch once ready."""
    fd, ready_path = tempfile.mkstemp()
    os.close(fd)
    os.unlink(ready_path)
    return ready_path


def _cleanup_ready_path(ready_path: str) -> None:
    """Remove the readiness marker if the child created it."""
    if os.path.exists(ready_path):
        os.unlink(ready_path)


def _wait_for_ready(ready_path: str) -> None:
    """Poll until the child signals it has registered its handler, then clean up."""
    try:
        wait_until(lambda: os.path.exists(ready_path), timeout=2.0, interval=0.01)
    finally:
        _cleanup_ready_path(ready_path)


def _spawn_sleep() -> subprocess.Popen[bytes]:
    """Spawn a Python child that exits cleanly on SIGTERM.

    The child installs a SIGTERM handler that calls ``sys.exit(0)`` so it
    exits promptly and predictably, then touches ``ready_path`` so the parent
    can wait_until the handler is actually registered instead of sleeping a
    fixed guess before sending the signal.
    """
    ready_path = _make_ready_path()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, sys; "
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
                f"pathlib.Path({ready_path!r}).touch(); "
                "import time; time.sleep(60)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_ready(ready_path)
    except TimeoutError:
        proc.kill()
        proc.wait()
        # The child is now guaranteed dead (proc.wait() returned), so this
        # check can't race with a late touch() the way _wait_for_ready's own
        # cleanup could if the child wrote the marker just before the raise.
        _cleanup_ready_path(ready_path)
        raise
    return proc


def _spawn_unkillable() -> subprocess.Popen[bytes]:
    """Spawn a child that traps SIGTERM and refuses to die."""
    ready_path = _make_ready_path()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, signal; "
                "signal.signal(signal.SIGTERM, lambda *_: None); "
                f"pathlib.Path({ready_path!r}).touch(); "
                "import time; time.sleep(60)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_ready(ready_path)
    except TimeoutError:
        proc.kill()
        proc.wait()
        # The child is now guaranteed dead (proc.wait() returned), so this
        # check can't race with a late touch() the way _wait_for_ready's own
        # cleanup could if the child wrote the marker just before the raise.
        _cleanup_ready_path(ready_path)
        raise
    return proc


def _reap(proc: subprocess.Popen[bytes]) -> None:
    """Reap the child so the OS releases the PID from the process table.

    When ``terminate()`` kills a child of the current process, the child
    becomes a zombie until the parent calls ``waitpid``. In production
    the target agents are NOT children of opensre, so ``os.kill(pid, 0)``
    would correctly fail. In tests we must reap explicitly.
    """
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class TestTerminate:
    def test_nonexistent_pid_raises(self) -> None:
        """Calling terminate() on a PID that doesn't exist should raise."""
        with pytest.raises(ProcessLookupError):
            terminate(999_999_999)

    @_skip_win32_posix_signals
    def test_sigterm_exits_promptly(self) -> None:
        """A normal child process should exit quickly after SIGTERM."""
        proc = _spawn_sleep()
        try:
            result = terminate(proc.pid, grace_s=5.0)
            # Reap the zombie so the process table entry is freed.
            _reap(proc)
            assert isinstance(result, TerminateResult)
            assert result.pid == proc.pid
            assert result.signal_sent in ("SIGTERM", "SIGKILL")
            # The process should actually be dead after reaping.
            with pytest.raises(ProcessLookupError):
                os.kill(proc.pid, 0)
        finally:
            proc.kill()
            proc.wait()

    @_skip_win32_posix_signals
    def test_process_is_gone_after_terminate(self) -> None:
        """After terminate() + reap, the PID should no longer exist."""
        proc = _spawn_sleep()
        try:
            terminate(proc.pid, grace_s=5.0)
            _reap(proc)
            with pytest.raises(ProcessLookupError):
                os.kill(proc.pid, 0)
        finally:
            proc.kill()
            proc.wait()

    @_skip_win32_posix_signals
    def test_no_zombie_left(self) -> None:
        """terminate() must not leave zombie processes after reaping."""
        proc = _spawn_sleep()
        try:
            terminate(proc.pid, grace_s=5.0)
            retcode = proc.wait(timeout=2)
            assert retcode is not None  # reaped, no zombie
        finally:
            proc.kill()
            proc.wait()

    @_skip_win32_posix_signals
    def test_force_kill_after_grace_period(self) -> None:
        """A process that traps SIGTERM should be SIGKILL'd after grace_s."""
        proc = _spawn_unkillable()
        try:
            result = terminate(proc.pid, grace_s=0.5)
            _reap(proc)
            assert result.signal_sent == "SIGKILL"
            # Verify the process is actually gone after reap.
            with pytest.raises(ProcessLookupError):
                os.kill(proc.pid, 0)
        finally:
            proc.kill()
            proc.wait()
