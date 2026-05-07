"""Per-PID resource snapshot for the monitor-local-agents fleet view.

Pure collector: one function, one PID, one snapshot. No background
loop, no caching, no UI wiring. The wiring layer (#1490) batches calls
in a REPL background task; the registry layer (#1487) decides which
PIDs to ask about.

The acceptance criterion for the parent issue (#1489) requires that
``psutil`` stay confined to this module so the dependency surface
remains explicit. ``app/agents/__init__.py`` reaches into here only via
explicit import.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import psutil

_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class ProcessSnapshot:
    """Single-instant resource snapshot for a process.

    Fields not available on the current platform or for the current
    user are ``None`` rather than raising — file descriptors are
    POSIX-only, and the connection count requires elevated privileges
    on some systems.
    """

    pid: int
    cpu_percent: float
    rss_mb: float
    num_fds: int | None
    num_connections: int | None
    status: str
    started_at: datetime


def probe(pid: int, *, cpu_interval: float = 0.1) -> ProcessSnapshot | None:
    """Return a one-shot resource snapshot for ``pid``.

    ``cpu_interval`` blocks for that many seconds to compute an
    accurate CPU percentage. Pass ``0.0`` for a non-blocking sample —
    the first such call returns ``0.0`` because psutil needs a delta
    baseline; callers that want accuracy without blocking should
    manage their own ``psutil.Process`` instances and call this
    function with ``cpu_interval=0.0`` on subsequent samples.

    Returns ``None`` for PIDs that don't exist or are zombies. Never
    raises ``psutil.NoSuchProcess`` or ``psutil.ZombieProcess``.
    """
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, ProcessLookupError):
        return None

    try:
        with proc.oneshot():
            cpu = proc.cpu_percent(interval=cpu_interval)
            rss_mb = proc.memory_info().rss / _BYTES_PER_MB
            num_fds = _safe_num_fds(proc)
            num_connections = _safe_num_connections(proc)
            status = proc.status()
            started_at = datetime.fromtimestamp(proc.create_time(), tz=UTC)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        # Process exited between the lookup and the field reads.
        return None

    return ProcessSnapshot(
        pid=pid,
        cpu_percent=cpu,
        rss_mb=rss_mb,
        num_fds=num_fds,
        num_connections=num_connections,
        status=status,
        started_at=started_at,
    )


def _safe_num_fds(proc: psutil.Process) -> int | None:
    """File-descriptor count is POSIX-only; ``None`` on Windows.

    EAFP rather than ``hasattr`` because typeshed's ``psutil.Process``
    declares ``num_fds`` unconditionally — the platform check only
    fires at runtime as ``AttributeError``.
    """
    try:
        return proc.num_fds()
    except (AttributeError, psutil.AccessDenied, NotImplementedError):
        return None


def _safe_num_connections(proc: psutil.Process) -> int | None:
    """Connection count requires elevated privileges on some platforms."""
    try:
        # ``net_connections`` is the modern name; older psutil only has
        # ``connections``. Probe for the new one first.
        method = getattr(proc, "net_connections", proc.connections)
        connections = method()
    except (psutil.AccessDenied, NotImplementedError):
        return None
    return len(connections)
