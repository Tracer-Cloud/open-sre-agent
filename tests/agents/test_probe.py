"""Tests for the per-PID resource probe (issue #1489)."""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import UTC, datetime
from unittest.mock import patch

import psutil

from app.agents.probe import ProcessSnapshot, probe

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PROBE_MODULE = _REPO_ROOT / "app" / "agents" / "probe.py"


def test_probe_returns_snapshot_for_self() -> None:
    """Probing the current Python process must return a populated snapshot."""
    snap = probe(os.getpid(), cpu_interval=0.0)

    assert snap is not None
    assert isinstance(snap, ProcessSnapshot)
    assert snap.pid == os.getpid()
    assert snap.rss_mb > 0.0, "interpreter + pytest should occupy non-zero RSS"
    assert snap.status, "status should be a non-empty string (e.g. 'running')"
    assert isinstance(snap.started_at, datetime)
    assert snap.started_at.tzinfo == UTC, "started_at must be tz-aware UTC"
    if sys.platform != "win32":
        assert snap.num_fds is not None and snap.num_fds > 0, (
            "POSIX systems should always report a positive FD count"
        )


def test_probe_returns_none_for_missing_pid() -> None:
    """Probing a PID that does not exist returns None, never raises."""
    # 2**31 - 1 is far above any realistic allocated PID on Linux/macOS
    # (kernel.pid_max is typically 32768 or 4194304).
    assert probe(2**31 - 1, cpu_interval=0.0) is None


def test_probe_returns_none_for_access_denied_process() -> None:
    """``cpu_percent()`` and ``memory_info()`` raise ``psutil.AccessDenied``
    for processes owned by another user on macOS and on Linux setups
    with restricted ``/proc``. The wiring layer treats both that and a
    truly missing PID as "no snapshot this tick" — the function must
    never let ``AccessDenied`` escape.
    """
    with patch.object(
        psutil.Process,
        "memory_info",
        side_effect=psutil.AccessDenied(pid=os.getpid()),
    ):
        assert probe(os.getpid(), cpu_interval=0.0) is None


def test_psutil_is_not_imported_outside_probe_module() -> None:
    """Acceptance criterion #3: ``psutil`` must stay confined to
    ``app/agents/probe.py`` so the dependency surface is explicit. A
    static scan over ``app/**/*.py`` catches future regressions
    deterministically — runtime import-graph checks would be flaky
    against lazy-import patterns the codebase already uses elsewhere.
    """
    leaks: list[str] = []
    for py_file in sorted((_REPO_ROOT / "app").rglob("*.py")):
        if py_file == _PROBE_MODULE:
            continue
        text = py_file.read_text(encoding="utf-8")
        for needle in ("import psutil", "from psutil"):
            if needle in text:
                leaks.append(f"{py_file.relative_to(_REPO_ROOT)} contains {needle!r}")
                break

    assert not leaks, (
        "psutil leaked into modules other than app/agents/probe.py:\n  " + "\n  ".join(leaks)
    )
