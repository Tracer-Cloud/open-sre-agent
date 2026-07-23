"""Background (daemon) lifecycle for the OpenSRE gateway process.

The CLI and the interactive shell both drive the daemon through these helpers:
a detached ``python -m surfaces.cli.gateway_entry`` child whose output is captured in
``~/.opensre/gateway/gateway.log`` and whose PID is tracked in ``gateway.pid``.
The running process reports per-component state (web app, Telegram chat, task
scheduler) through ``components.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

GATEWAY_LOG_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "gateway.log"
GATEWAY_PID_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "gateway.pid"
GATEWAY_COMPONENTS_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "components.json"


def gateway_daemon_pid() -> int | None:
    """Return the live daemon PID, clearing a stale pidfile on the way."""
    try:
        pid = int(GATEWAY_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    if _alive(pid):
        return pid
    GATEWAY_PID_FILE.unlink(missing_ok=True)
    return None


def start_gateway_daemon(
    *, startup_wait: float = 2.0, argv: Sequence[str] | None = None
) -> tuple[bool, str]:
    """Spawn the gateway as a detached background process.

    Returns ``(ok, message)``. Starting an already-running gateway is a no-op
    success; a child that dies during ``startup_wait`` is a failure and the
    message carries the log tail.
    """
    if (pid := gateway_daemon_pid()) is not None:
        return True, f"OpenSRE gateway already running (pid {pid})."

    GATEWAY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with GATEWAY_LOG_FILE.open("ab") as log:
        process = subprocess.Popen(
            argv or (sys.executable, "-m", "surfaces.cli.gateway_entry"),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    GATEWAY_PID_FILE.write_text(f"{process.pid}\n")

    deadline = time.monotonic() + startup_wait
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.1)
    if process.poll() is not None:
        GATEWAY_PID_FILE.unlink(missing_ok=True)
        tail = read_gateway_log_tail(10) or "(log empty)"
        return False, f"OpenSRE gateway exited during startup:\n{tail}"
    return True, f"OpenSRE gateway started (pid {process.pid})."


def stop_gateway_daemon(*, timeout: float = 10.0) -> tuple[bool, str]:
    """SIGTERM the daemon, escalating to SIGKILL when it exceeds *timeout*."""
    pid = gateway_daemon_pid()
    if pid is None:
        return True, "OpenSRE gateway is not running."

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            _clear_runtime_files()
            return True, f"OpenSRE gateway stopped (pid {pid})."
        time.sleep(0.2)

    # A long-poll or in-flight turn can outlive the graceful window — force it.
    os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)
    if _alive(pid):
        return False, f"OpenSRE gateway (pid {pid}) survived SIGKILL."
    _clear_runtime_files()
    return True, f"OpenSRE gateway force-killed after {timeout:g}s (pid {pid})."


def _clear_runtime_files() -> None:
    GATEWAY_PID_FILE.unlink(missing_ok=True)
    clear_component_status()


def write_component_status(components: dict[str, str]) -> None:
    """Record the running process's per-component state (called by the manager)."""
    GATEWAY_COMPONENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": time.time(), "components": components}
    GATEWAY_COMPONENTS_FILE.write_text(json.dumps(payload, indent=2))


def read_component_status() -> dict[str, str]:
    """Return the live process's component states ({} when it is not running)."""
    try:
        payload = json.loads(GATEWAY_COMPONENTS_FILE.read_text())
        pid = int(payload["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if pid <= 0 or not _alive(pid):
        return {}
    return {str(name): str(detail) for name, detail in payload.get("components", {}).items()}


def clear_component_status() -> None:
    GATEWAY_COMPONENTS_FILE.unlink(missing_ok=True)


def read_gateway_log_tail(lines: int = 50) -> str:
    """Return the last *lines* of the gateway log ('' when there is none)."""
    try:
        with GATEWAY_LOG_FILE.open("r", errors="replace") as log:
            return "".join(deque(log, maxlen=lines)).rstrip("\n")
    except OSError:
        return ""


def _alive(pid: int) -> bool:
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)  # reap first if the daemon is our own child
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by another user
    return True


__all__ = [
    "GATEWAY_COMPONENTS_FILE",
    "GATEWAY_LOG_FILE",
    "GATEWAY_PID_FILE",
    "clear_component_status",
    "gateway_daemon_pid",
    "read_component_status",
    "read_gateway_log_tail",
    "start_gateway_daemon",
    "stop_gateway_daemon",
    "write_component_status",
]
