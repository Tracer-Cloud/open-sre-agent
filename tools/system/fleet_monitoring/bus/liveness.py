"""PID file management and process/socket liveness probes."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path


def _pid_file_for(socket_path: Path) -> Path:
    """Return the sidecar PID-file path for a given bus socket path."""
    return socket_path.with_name(socket_path.name + ".pid")


def _read_broker_pid(socket_path: Path) -> int | None:
    """Read the broker PID from the sidecar file, or ``None`` if missing/garbled."""
    pid_path = _pid_file_for(socket_path)
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _process_is_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` probe: True iff the PID maps to a live process we can signal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it. Treat as alive — we still can't
        # safely unlink the socket out from under whoever owns it.
        return True
    except OSError:
        return False
    return True


def _socket_is_live(path: Path) -> bool:
    """Return True if a broker is currently listening on ``path``.

    Uses a PID-file side channel rather than connecting to the socket: the
    broker writes its PID on ``start()`` and removes it on ``stop()``. We treat
    the broker as live iff the socket file exists, the PID file exists, and
    the recorded PID maps to a process we can signal. This avoids creating a
    short-lived phantom subscriber + reader thread on every ``publish()`` /
    ``subscribe()`` call by a non-owner process.

    A stale PID file (broker crashed without cleanup) is reported as not-live;
    the caller's ``_unlink_stale`` path will remove the socket file and rebind.
    """
    if not path.exists():
        return False
    pid = _read_broker_pid(path)
    if pid is None:
        return False
    return _process_is_alive(pid)


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def _unlink_stale(path: Path) -> None:
    """Remove a socket file (and its sidecar PID file) that has no live listener."""
    with suppress(FileNotFoundError, OSError):
        os.unlink(path)
    with suppress(FileNotFoundError, OSError):
        os.unlink(_pid_file_for(path))


def _write_pid_file_atomic(path: Path, pid: int) -> None:
    """Write ``pid`` to the sidecar atomically (tmpfile + rename).

    Raises ``OSError`` on failure. Callers (i.e. ``BusServer.start``) must
    treat a missing PID file as a hard error: in multi-process operation,
    ``_socket_is_live`` reads the sidecar, and silently swallowing a write
    failure would let peers see the broker as dead, ``_unlink_stale`` its
    socket file out from under it, and silently split the bus.
    """
    pid_path = _pid_file_for(path)
    tmp = pid_path.with_name(pid_path.name + ".tmp")
    try:
        tmp.write_text(str(pid), encoding="utf-8")
        with suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, pid_path)
    except OSError:
        with suppress(FileNotFoundError, OSError):
            os.unlink(tmp)
        raise
