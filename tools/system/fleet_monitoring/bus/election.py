"""Cross-process flock broker election and lifecycle management."""

from __future__ import annotations

import errno
import os
import threading
import types
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from . import liveness, server

_fcntl: types.ModuleType | None
try:
    import fcntl as _fcntl_impl
except ImportError:
    # ``fcntl`` is POSIX-only; PyInstaller Windows binaries must import this
    # module without failing. Cross-process broker election falls back to
    # bind/PID-file checks when ``flock`` is unavailable (see ``_ensure_broker``).
    _fcntl = None
else:
    _fcntl = _fcntl_impl

_broker_lock = threading.Lock()
_brokers: dict[Path, server.BusServer] = {}

_BIND_RACE_ERRNOS: frozenset[int] = frozenset({errno.EADDRINUSE, errno.EEXIST})


def _election_lock_path(socket_path: Path) -> Path:
    """Sidecar lock file used to serialize broker election across processes."""
    return socket_path.with_name(socket_path.name + ".lock")


def _acquire_election_flock(path: Path) -> int | None:
    """Open the election lock file and acquire an exclusive ``flock``.

    Returns the open fd on success, or ``None`` if the lock could not be
    obtained (file system without ``flock`` support, permission denied,
    Windows, ...). The caller is responsible for releasing + closing the fd via
    ``_release_election_flock``.
    """
    if _fcntl is None:
        return None
    lock_path = _election_lock_path(path)
    try:
        liveness._ensure_parent_dir(lock_path)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
    except OSError:
        with suppress(OSError):
            os.close(fd)
        return None
    return fd


def _release_election_flock(fd: int | None) -> None:
    if fd is None:
        return
    if _fcntl is not None:
        with suppress(OSError):
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    with suppress(OSError):
        os.close(fd)


@contextmanager
def _hold_election_flock(path: Path) -> Iterator[None]:
    """Acquire the cross-process election flock for ``path`` for one ``with`` block.

    The fd lifecycle (``os.open`` → ``flock`` → ``flock LOCK_UN`` → ``os.close``)
    lives entirely in this scope so static analyzers can verify the file is
    always closed (CodeQL ``py/file-not-closed``). The standalone
    ``_acquire_election_flock`` / ``_release_election_flock`` helpers are kept
    for tests that exercise the half-paired primitive directly.
    """
    if _fcntl is None:
        # No flock support (Windows, exotic FS). Matches the
        # ``_acquire_election_flock`` → ``None`` contract: yield without
        # holding a cross-process lock.
        yield
        return

    lock_path = _election_lock_path(path)
    try:
        liveness._ensure_parent_dir(lock_path)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield
        return

    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        except OSError:
            # Could not acquire flock; proceed without it (best-effort
            # election, matching the original ``None``-on-failure contract).
            yield
            return
        try:
            yield
        finally:
            with suppress(OSError):
                _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        with suppress(OSError):
            os.close(fd)


def _ensure_broker(path: Path) -> server.BusServer | None:
    """Elect a broker for ``path`` if none is live, else return ``None``.

    Idempotent per-path: if this process already owns the broker, returns the
    existing instance. If another process owns it, returns ``None`` (the caller
    should connect as a client). If a stale socket file exists, unlinks it and
    retries the bind.

    Cross-process election is serialized by a POSIX ``flock`` on a sidecar
    lock file (``<socket>.lock``) when ``fcntl`` is available (not on Windows).
    Without ``flock``, two processes that both
    observe ``_socket_is_live`` → False can race through ``_unlink_stale`` +
    ``bind``: the kernel guarantees one bind succeeds, but the loser is left
    holding a listener fd whose filesystem path the winner just took, plus
    the accept/reader daemon threads it spawned — a real resource leak that
    persists for the loser's process lifetime. Where ``flock`` is available,
    holding it around the
    check-then-bind sequence makes election atomic across processes.

    A lost bind race (``EADDRINUSE`` / ``EEXIST``) is still converted to
    ``None`` defensively — flock is best-effort on exotic filesystems. Any
    other ``OSError`` from ``start()`` (e.g. PID-file write failure) is
    propagated — those are real errors users need to see, not bus splits to
    paper over silently.
    """
    # Fast in-process path: if we already own a running broker, no
    # cross-process work is needed.
    with _broker_lock:
        existing = _brokers.get(path)
        if existing is not None and existing.is_running:
            return existing

    with _hold_election_flock(path), _broker_lock:
        existing = _brokers.get(path)
        if existing is not None and existing.is_running:
            return existing
        if liveness._socket_is_live(path):
            return None
        liveness._unlink_stale(path)
        srv = server.BusServer(path)
        try:
            srv.start()
        except OSError as exc:
            if exc.errno in _BIND_RACE_ERRNOS:
                return None
            raise
        _brokers[path] = srv
        return srv
