"""Tests for agent lifecycle management (SIGTERM → SIGKILL)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from app.agents.lifecycle import TerminateResult, terminate


def _spawn_sleep() -> subprocess.Popen[bytes]:
    """Spawn a Python child that exits cleanly on SIGTERM.

    The child installs a SIGTERM handler that calls ``sys.exit(0)``
    so it exits promptly and predictably.
    """
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal, sys, time; "
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
                "time.sleep(60)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give the child a moment to register its signal handler.
    time.sleep(0.2)
    return proc


def _spawn_unkillable() -> subprocess.Popen[bytes]:
    """Spawn a child that traps SIGTERM and refuses to die."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            ("import signal, time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(60)"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
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

    def test_nonexistent_pid_raises(self) -> None:
        """Calling terminate() on a PID that doesn't exist should raise."""
        with pytest.raises(ProcessLookupError):
            terminate(999_999_999)

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
