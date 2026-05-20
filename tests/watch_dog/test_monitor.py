"""Tests for the psutil-backed process monitor."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import cast

import psutil
import pytest

from app.cli.support.errors import OpenSREError
from app.watch_dog.config import WatchdogConfig
from app.watch_dog.process_monitor import ProcessMonitor


@dataclass
class _MemoryInfo:
    rss: int


class _FakeProcess:
    def __init__(
        self,
        pid: int,
        name: str,
        *,
        cmdline: tuple[str, ...] = ("python", "worker.py"),
        rss: int = 1024,
        cpu_values: tuple[float, ...] = (0.0, 17.5),
        create_time: float | None = None,
    ) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "cmdline": list(cmdline),
            "create_time": create_time or (time.time() - 60),
        }
        self._name = name
        self._cmdline = cmdline
        self._rss = rss
        self._cpu_values = list(cpu_values)
        self.cpu_calls = 0

    def is_running(self) -> bool:
        return True

    def name(self) -> str:
        return self._name

    def cmdline(self) -> list[str]:
        return list(self._cmdline)

    def cpu_percent(self, interval: float | None = None) -> float:
        _ = interval
        self.cpu_calls += 1
        if self._cpu_values:
            return self._cpu_values.pop(0)
        return 0.0

    def memory_info(self) -> _MemoryInfo:
        return _MemoryInfo(rss=self._rss)

    def create_time(self) -> float:
        return float(cast(float, self.info["create_time"]))


class _GoneProcess(_FakeProcess):
    def is_running(self) -> bool:
        return False


class _AccessDeniedProcess(_FakeProcess):
    def memory_info(self) -> _MemoryInfo:
        raise psutil.AccessDenied(self.pid)


def test_process_monitor_resolves_by_pid_and_warms_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(123, "python", cpu_values=(0.0, 22.0))
    monkeypatch.setattr("app.watch_dog.process_monitor.process_probe.process", lambda _pid: process)

    monitor = ProcessMonitor(WatchdogConfig(pid=123, max_cpu=90))
    sample = monitor.sample()

    assert process.cpu_calls == 2
    assert sample.pid == 123
    assert sample.name == "python"
    assert sample.cpu_percent == 22.0
    assert sample.rss_bytes == 1024
    assert sample.alive is True


def test_process_monitor_resolves_name_regex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _FakeProcess(20, "python")
    second = _FakeProcess(10, "claude")
    monkeypatch.setattr(
        "app.watch_dog.process_monitor.process_probe.process_iter",
        lambda _attrs: iter([first, second]),
    )

    monitor = ProcessMonitor(WatchdogConfig(name="claude", max_cpu=90))

    assert monitor.sample().pid == 10


def test_process_monitor_requires_pick_first_for_multiple_name_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.watch_dog.process_monitor.process_probe.process_iter",
        lambda _attrs: iter([_FakeProcess(20, "claude"), _FakeProcess(10, "claude")]),
    )

    with pytest.raises(OpenSREError, match="Multiple processes"):
        ProcessMonitor(WatchdogConfig(name="claude", max_cpu=90))

    monitor = ProcessMonitor(WatchdogConfig(name="claude", pick_first=True, max_cpu=90))
    assert monitor.sample().pid == 10


def test_process_monitor_returns_dead_sample_on_no_such_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _GoneProcess(123, "python")
    monkeypatch.setattr("app.watch_dog.process_monitor.process_probe.process", lambda _pid: process)

    monitor = ProcessMonitor(WatchdogConfig(pid=123, max_cpu=90))
    sample = monitor.sample()

    assert sample.pid == 123
    assert sample.alive is False
    assert sample.cpu_percent == 0.0


def test_process_monitor_returns_dead_sample_on_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _AccessDeniedProcess(123, "python")
    monkeypatch.setattr("app.watch_dog.process_monitor.process_probe.process", lambda _pid: process)

    monitor = ProcessMonitor(WatchdogConfig(pid=123, max_cpu=90))
    sample = monitor.sample()

    assert sample.pid == 123
    assert sample.alive is False
