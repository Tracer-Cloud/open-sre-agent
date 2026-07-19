"""Process sampling primitives for the watchdog CLI."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from platform.common.errors import OpenSREError
from platform.common.exit_codes import ERROR
from tools.system.fleet_monitoring import probe as process_probe
from tools.system.watch_dog.config import WatchdogConfig

# A permission denial can be a momentary race; retry a few times before
# declaring the process permanently unreadable so a transient denial does
# not abort a healthy watch.
_DEFAULT_ACCESS_ATTEMPTS = 3
_DEFAULT_ACCESS_RETRY_SECONDS = 0.5


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

    def __init__(
        self,
        config: WatchdogConfig,
        *,
        max_access_attempts: int = _DEFAULT_ACCESS_ATTEMPTS,
        access_retry_seconds: float = _DEFAULT_ACCESS_RETRY_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._process = _resolve_process(config)
        self._pid = self._process.pid
        self._name = _safe_process_name(self._process)
        self._cmdline = _safe_cmdline(self._process)
        self._started_at = _safe_create_time(self._process)
        self._max_access_attempts = max(1, max_access_attempts)
        self._access_retry_seconds = access_retry_seconds
        self._sleep = sleep
        self._warm_cpu_percent()

    def sample(self) -> ProcessSample:
        """Capture CPU, RSS, runtime, and liveness for the target process.

        A ``NoSuchProcess`` (or an ``AccessDenied`` whose PID is already gone)
        is a genuine exit and yields a dead sample. An ``AccessDenied`` while
        the PID is still alive means the process is running but unreadable
        (e.g. a root-owned daemon on macOS); that is never a clean exit, so we
        retry a few times to ride out a transient denial and otherwise raise.
        """
        attempt = 0
        while True:
            try:
                return self._read_sample()
            except process_probe.PROCESS_NOT_FOUND:
                return self._dead_sample()
            except process_probe.PROCESS_ACCESS_DENIED as exc:
                if not process_probe.pid_exists(self._pid):
                    return self._dead_sample()
                attempt += 1
                if attempt >= self._max_access_attempts:
                    raise self._inaccessible_error() from exc
                self._sleep(self._access_retry_seconds)

    def _read_sample(self) -> ProcessSample:
        if not self._process.is_running():
            return self._dead_sample()
        name = self._process.name()
        cmdline = tuple(self._process.cmdline())
        cpu_percent = float(self._process.cpu_percent(interval=None))
        rss_bytes = int(self._process.memory_info().rss)
        started_at = float(self._process.create_time())

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

    def _inaccessible_error(self) -> OpenSREError:
        label = self._name or "?"
        return OpenSREError(
            f"Process {self._pid} ({label}) is running but cannot be inspected "
            "(permission denied).",
            suggestion=(
                "Re-run with sufficient privileges (for example under sudo) to monitor "
                "this process, or target a process your user owns."
            ),
            exit_code=ERROR,
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
