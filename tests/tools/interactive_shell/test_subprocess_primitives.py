"""Unit tests for pure subprocess helpers in tools.interactive_shell.subprocess."""

from __future__ import annotations

import subprocess
import tempfile
import threading

import pytest

from tools.interactive_shell.subprocess import (
    read_diag,
    subprocess_env_with_width,
    terminate_child_process,
    watch_subprocess_until_exit,
)


def test_subprocess_env_with_width_reserves_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINES", raising=False)
    env = subprocess_env_with_width(columns=100, lines=30)
    assert env["COLUMNS"] == "81"
    assert env["LINES"] == "30"


def test_subprocess_env_with_width_preserves_existing_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINES", "24")
    env = subprocess_env_with_width(columns=100, lines=30)
    assert env["LINES"] == "24"


def test_read_diag_strips_ansi() -> None:
    with tempfile.SpooledTemporaryFile(max_size=4096) as buf:
        buf.write(b"\x1b[31merror\x1b[0m")
        buf.seek(0)
        assert read_diag(buf) == "error"


def test_terminate_child_process_noop_when_exited() -> None:
    proc = subprocess.Popen(["true"])
    proc.wait()
    terminate_child_process(proc)


def test_watch_subprocess_until_exit_on_cancel() -> None:
    proc = subprocess.Popen(["sleep", "30"])
    cancel = threading.Event()
    cancel.set()
    result = watch_subprocess_until_exit(
        proc,
        cancel_event=cancel,
        timeout_seconds=60,
    )
    assert result.cancelled
    assert result.terminated_by_watcher
