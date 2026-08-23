"""Publisher socket connection pooling and background draining."""

from __future__ import annotations

import atexit
import socket
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


def _connect_client(path: Path, timeout: float) -> socket.socket:
    """Open a blocking UDS connection to the broker at ``path``."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
    except OSError:
        with suppress(OSError):
            client.close()
        raise
    client.settimeout(None)
    return client


@dataclass
class _CachedPublisher:
    """A persistent publisher connection plus the bookkeeping to share it safely.

    ``send_lock`` serializes ``sendall`` from concurrent publish() calls in the
    same process so frames don't interleave on the wire. ``drain_thread`` is a
    daemon that reads-and-discards anything the broker fans back to us — under
    multi-publisher load the broker would otherwise fill our kernel recv buffer
    with peers' frames, hit the write-timeout in ``_broadcast``, and evict our
    connection. Draining keeps the cached socket usable indefinitely.
    """

    sock: socket.socket
    send_lock: threading.Lock
    drain_thread: threading.Thread


_publisher_lock = threading.Lock()
_publishers: dict[Path, _CachedPublisher] = {}


def _drain_publisher_socket(sock: socket.socket) -> None:
    """Read-and-discard everything the broker sends to a cached publisher.

    Exits silently on EOF or socket error — at that point the cache entry
    will already have been (or is about to be) invalidated by the publish
    retry path.
    """
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return
    except OSError:
        return


def _open_cached_publisher(path: Path, *, connect_timeout: float) -> _CachedPublisher:
    """Connect a fresh publisher and start its drain thread. Caller holds no lock."""
    sock = _connect_client(path, timeout=connect_timeout)
    cached = _CachedPublisher(
        sock=sock,
        send_lock=threading.Lock(),
        drain_thread=threading.Thread(
            target=_drain_publisher_socket,
            args=(sock,),
            name="agents-bus-publisher-drain",
            daemon=True,
        ),
    )
    cached.drain_thread.start()
    return cached


def _get_or_open_publisher(path: Path, *, connect_timeout: float) -> _CachedPublisher:
    """Return a cached publisher for ``path``, opening one if none exists."""
    with _publisher_lock:
        existing = _publishers.get(path)
        if existing is not None:
            return existing
    # Open outside the lock so concurrent first-publishers don't all serialize
    # behind a slow connect.
    fresh = _open_cached_publisher(path, connect_timeout=connect_timeout)
    with _publisher_lock:
        existing = _publishers.get(path)
        if existing is not None:
            # Lost the race; close ours and reuse theirs.
            with suppress(OSError):
                fresh.sock.close()
            return existing
        _publishers[path] = fresh
        return fresh


def _drop_publisher(path: Path, sock: socket.socket) -> None:
    """Remove the cached publisher for ``path`` if it still references ``sock``."""
    with _publisher_lock:
        cached = _publishers.get(path)
        if cached is not None and cached.sock is sock:
            del _publishers[path]
        else:
            cached = None
    if cached is not None:
        with suppress(OSError):
            cached.sock.close()


def _close_all_publishers() -> None:
    """Drop every cached publisher (e.g. at process exit). Safe to call repeatedly."""
    with _publisher_lock:
        sockets = [c.sock for c in _publishers.values()]
        _publishers.clear()
    for sock in sockets:
        with suppress(OSError):
            sock.close()


atexit.register(_close_all_publishers)
