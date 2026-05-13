"""Process sampling primitives for the watchdog CLI."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.agents import probe as process_probe
from app.cli.support.errors import OpenSREError
from app.watch_dog.config import WatchdogConfig


@dataclass(frozen=True)
class ProcessSample:
    """A point-in-time process resource sample."""

    pid: int
    name: str
    cmdline: tuple[str, ...]
    cpu_percent: float
    rss_bytes: int
    runtime_seconds: float
    alive: bool
    started_at: float | None = None

    @property
    def command(self) -> str:
        """Return a display-friendly command string."""
        return " ".join(self.cmdline)


class Sampler(Protocol):
    """Protocol used by the runner so tests can inject fake samples."""

    def sample(self) -> ProcessSample:
        """Return the next process sample."""


class ProcessMonitor:
    """Resolve and sample one process."""

    def __init__(self, config: WatchdogConfig) -> None:
        self._process = _resolve_process(config)
        self._pid = self._process.pid
        self._name = _safe_process_name(self._process)
        self._cmdline = _safe_cmdline(self._process)
        self._started_at = _safe_create_time(self._process)
        self._warm_cpu_percent()

    def sample(self) -> ProcessSample:
        """Capture CPU, RSS, runtime, and liveness for the target process."""
        try:
            if not self._process.is_running():
                return self._dead_sample()
            name = self._process.name()
            cmdline = tuple(self._process.cmdline())
            cpu_percent = float(self._process.cpu_percent(interval=None))
            rss_bytes = int(self._process.memory_info().rss)
            started_at = float(self._process.create_time())
        except process_probe.PROCESS_INACCESSIBLE_OR_GONE:
            return self._dead_sample()

        return ProcessSample(
            pid=self._pid,
            name=name,
            cmdline=cmdline,
            cpu_percent=cpu_percent,
            rss_bytes=rss_bytes,
            runtime_seconds=max(0.0, time.time() - started_at),
            alive=True,
            started_at=started_at,
        )

    def _warm_cpu_percent(self) -> None:
        try:
            self._process.cpu_percent(interval=None)
        except process_probe.PROCESS_ERROR:
            return

    def _dead_sample(self) -> ProcessSample:
        return ProcessSample(
            pid=self._pid,
            name=self._name,
            cmdline=self._cmdline,
            cpu_percent=0.0,
            rss_bytes=0,
            runtime_seconds=0.0,
            alive=False,
            started_at=self._started_at,
        )


def _resolve_process(config: WatchdogConfig) -> Any:
    if config.pid is not None:
        try:
            return process_probe.process(config.pid)
        except process_probe.PROCESS_NOT_FOUND as exc:
            raise OpenSREError(
                f"No process found for PID {config.pid}.",
                suggestion="Check the PID and retry while the process is still running.",
            ) from exc

    assert config.name is not None
    return _resolve_process_by_name(config.name, pick_first=config.pick_first)


def _resolve_process_by_name(pattern: str, *, pick_first: bool) -> Any:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise OpenSREError(
            f"Invalid --name regex: {exc}",
            suggestion="Pass a valid Python regular expression, for example --name claude.",
        ) from exc

    matches: list[Any] = []
    for process in process_probe.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            name = str(process.info.get("name") or "")
        except process_probe.PROCESS_INACCESSIBLE_OR_GONE:
            continue
        if compiled.search(name):
            matches.append(process)

    matches.sort(key=lambda proc: proc.pid)
    if not matches:
        raise OpenSREError(
            f"No running process name matched {pattern!r}.",
            suggestion="Run `ps aux` to confirm the process name, then retry.",
        )
    if len(matches) > 1 and not pick_first:
        preview = ", ".join(f"{proc.pid}:{_safe_process_name(proc)}" for proc in matches[:5])
        raise OpenSREError(
            f"Multiple processes matched {pattern!r}: {preview}",
            suggestion="Pass --pid for the exact process or --pick-first to use the lowest PID.",
        )
    return matches[0]


def _safe_process_name(process: Any) -> str:
    try:
        return str(process.name())
    except process_probe.PROCESS_ERROR:
        return str(getattr(process, "info", {}).get("name") or "")


def _safe_cmdline(process: Any) -> tuple[str, ...]:
    try:
        return tuple(process.cmdline())
    except process_probe.PROCESS_ERROR:
        return tuple(getattr(process, "info", {}).get("cmdline") or ())


def _safe_create_time(process: Any) -> float | None:
    try:
        return float(process.create_time())
    except process_probe.PROCESS_ERROR:
        value = getattr(process, "info", {}).get("create_time")
        return float(value) if value is not None else None
