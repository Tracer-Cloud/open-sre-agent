"""Tests for the Telegram gateway daemon lifecycle helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gateway.runtime import daemon

_SLEEPER = (sys.executable, "-c", "import time; time.sleep(30)")
_CRASHER = (sys.executable, "-c", "print('boom'); raise SystemExit(1)")


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(daemon, "GATEWAY_PID_FILE", tmp_path / "gateway.pid")
    monkeypatch.setattr(daemon, "GATEWAY_LOG_FILE", tmp_path / "gateway.log")
    monkeypatch.setattr(daemon, "GATEWAY_COMPONENTS_FILE", tmp_path / "components.json")
    yield
    daemon.stop_gateway_daemon(timeout=5.0)


def test_pid_is_none_without_pidfile() -> None:
    assert daemon.gateway_daemon_pid() is None


def test_stale_pidfile_is_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon.GATEWAY_PID_FILE.write_text("12345\n")
    monkeypatch.setattr(daemon, "_alive", lambda _pid: False)

    assert daemon.gateway_daemon_pid() is None
    assert not daemon.GATEWAY_PID_FILE.exists()


def test_garbage_pidfile_is_ignored() -> None:
    daemon.GATEWAY_PID_FILE.write_text("not-a-pid\n")
    assert daemon.gateway_daemon_pid() is None


def test_start_stop_round_trip() -> None:
    ok, message = daemon.start_gateway_daemon(startup_wait=0.3, argv=_SLEEPER)
    assert ok, message
    pid = daemon.gateway_daemon_pid()
    assert pid is not None
    assert f"pid {pid}" in message

    ok, message = daemon.start_gateway_daemon(startup_wait=0.3, argv=_SLEEPER)
    assert ok
    assert "already running" in message

    ok, message = daemon.stop_gateway_daemon(timeout=5.0)
    assert ok, message
    assert daemon.gateway_daemon_pid() is None
    assert not daemon.GATEWAY_PID_FILE.exists()


def test_start_reports_startup_crash_with_log_tail() -> None:
    ok, message = daemon.start_gateway_daemon(startup_wait=5.0, argv=_CRASHER)
    assert not ok
    assert "exited during startup" in message
    assert "boom" in message
    assert daemon.gateway_daemon_pid() is None


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored() -> None:
    stubborn = (
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
    )
    ok, message = daemon.start_gateway_daemon(startup_wait=0.5, argv=stubborn)
    assert ok, message

    ok, message = daemon.stop_gateway_daemon(timeout=1.0)
    assert ok, message
    assert "force-killed" in message
    assert daemon.gateway_daemon_pid() is None


def test_stop_when_not_running_is_ok() -> None:
    ok, message = daemon.stop_gateway_daemon()
    assert ok
    assert "not running" in message


def test_component_status_round_trip() -> None:
    components = {"web": "serving :8000", "telegram": "polling", "scheduler": "idle"}
    daemon.write_component_status(components)

    assert daemon.read_component_status() == components  # writer pid (ours) is alive

    daemon.clear_component_status()
    assert daemon.read_component_status() == {}


def test_component_status_ignored_when_process_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon.write_component_status({"web": "serving :8000"})
    monkeypatch.setattr(daemon, "_alive", lambda _pid: False)

    assert daemon.read_component_status() == {}


def test_log_tail_returns_last_lines() -> None:
    daemon.GATEWAY_LOG_FILE.write_text("".join(f"line-{i}\n" for i in range(100)))
    tail = daemon.read_gateway_log_tail(3)
    assert tail == "line-97\nline-98\nline-99"
    assert daemon.read_gateway_log_tail(0) == ""
