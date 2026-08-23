"""In-process broker server that fans JSONL frames out to subscribers."""

from __future__ import annotations

import logging
import os
import select
import socket
import threading
from contextlib import suppress
from pathlib import Path

from . import liveness

logger = logging.getLogger(__name__)

#: Max bytes per JSONL frame on the wire. Frames over this are dropped with a
#: warning; a finding payload that big is almost certainly a bug.
_MAX_FRAME_BYTES: int = 64 * 1024

#: Per-subscriber write deadline used by ``BusServer._broadcast``. A subscriber
#: whose kernel recv buffer is full for longer than this is considered
#: unresponsive and evicted, so one wedged client cannot stall fan-out for
#: every other publisher's reader thread.
_BROADCAST_WRITE_TIMEOUT_SECONDS: float = 0.2


class BusServer:
    """In-process broker that fans JSONL frames out to every connected subscriber.

    The first publisher or subscriber on a given socket path elects itself as
    broker by calling ``BusServer(path).start()``. The server runs an accept
    loop and per-connection reader threads as daemons, so the host process
    exits without needing to join them. Subscribers that disconnect or fail to
    receive are removed from the fan-out set on the next broadcast.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._listener: socket.socket | None = None
        # Map of subscriber socket -> per-connection write lock. Concurrent
        # broadcasts from multiple publisher reader-threads to the same
        # subscriber socket would otherwise interleave bytes mid-frame
        # (``sendall`` is multi-syscall for frames near the 64 KiB cap),
        # producing a garbled JSONL line the subscriber cannot parse. The
        # lock is per-subscriber so broadcasts to *different* subscribers
        # still proceed in parallel.
        self._subscribers: dict[socket.socket, threading.Lock] = {}
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._accept_thread: threading.Thread | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        """Bind the socket, write the PID sidecar, and spawn the accept loop.

        Raises ``OSError`` on bind failure or on PID-file write failure (the
        sidecar is required for correct multi-process liveness; see
        ``_write_pid_file_atomic``). Any partial state is rolled back so a
        half-started broker never persists.
        """
        if self._running.is_set():
            return
        liveness._ensure_parent_dir(self._path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._path))
        except OSError:
            listener.close()
            raise
        with suppress(OSError):
            os.chmod(self._path, 0o600)
        listener.listen(16)
        # Publish our PID via the sidecar so peers can answer "is the broker
        # live?" without making a real connection (which would otherwise spawn
        # a short-lived phantom subscriber on every probe). If this fails we
        # tear the bind down so a peer doesn't ``_unlink_stale`` our orphaned
        # socket file out from under us — ``_socket_is_live`` reads the
        # sidecar, and a missing one would silently split the bus.
        try:
            liveness._write_pid_file_atomic(self._path, os.getpid())
        except OSError:
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                listener.close()
            with suppress(FileNotFoundError, OSError):
                os.unlink(self._path)
            raise
        self._listener = listener
        self._running.set()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="agents-bus-accept",
            daemon=True,
        )
        self._accept_thread.start()

    def stop(self) -> None:
        """Shut the broker down: close the listener, drop all subscribers, unlink the socket."""
        if not self._running.is_set():
            return
        self._running.clear()
        listener, self._listener = self._listener, None
        if listener is not None:
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                listener.close()
        with self._lock:
            for sub in self._subscribers:
                with suppress(OSError):
                    sub.close()
            self._subscribers.clear()
        liveness._unlink_stale(self._path)

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while self._running.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                # Listener closed during ``stop()`` — exit cleanly.
                return
            conn.setblocking(True)
            with self._lock:
                self._subscribers[conn] = threading.Lock()
            reader = threading.Thread(
                target=self._reader_loop,
                args=(conn,),
                name="agents-bus-reader",
                daemon=True,
            )
            reader.start()

    def _reader_loop(self, conn: socket.socket) -> None:
        """Read newline-delimited frames from one client and broadcast them."""
        buf = b""
        try:
            while self._running.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > _MAX_FRAME_BYTES * 4:
                    logger.warning("bus client exceeded buffer cap; disconnecting")
                    return
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line:
                        continue
                    if len(line) > _MAX_FRAME_BYTES:
                        logger.warning("dropping oversized bus frame (%d bytes)", len(line))
                        continue
                    self._broadcast(line + b"\n", origin=conn)
        except OSError:
            return
        finally:
            self._drop_subscriber(conn)

    def _broadcast(self, frame: bytes, origin: socket.socket | None) -> None:
        with self._lock:
            # Snapshot (sub, write_lock) pairs so concurrent broadcasts to
            # different subscribers can proceed in parallel — only writes to
            # the *same* subscriber are serialized.
            targets = list(self._subscribers.items())
        dead: list[socket.socket] = []
        for sub, write_lock in targets:
            if sub is origin:
                # Don't echo a publisher's own frame back to itself.
                continue
            try:
                # Per-subscriber write lock prevents two publisher reader-
                # threads from interleaving bytes mid-frame on the same
                # socket (``sendall`` may issue multiple ``send`` syscalls
                # for large frames). Different subscribers have independent
                # locks, so cross-subscriber fan-out is unaffected.
                with write_lock:
                    # Write-readiness gate via ``select``: a blocking
                    # ``sendall`` on a subscriber whose kernel recv buffer is
                    # full would wedge the reader thread of *every*
                    # publisher, freezing fan-out across the bus. Using
                    # ``select`` instead of ``sub.settimeout`` so the
                    # per-connection ``_reader_loop``'s ``recv`` is
                    # unaffected (a quiet healthy subscriber must not be
                    # evicted).
                    _r, ready, _x = select.select([], [sub], [], _BROADCAST_WRITE_TIMEOUT_SECONDS)
                    if not ready:
                        logger.warning("bus subscriber unresponsive; evicting from fan-out")
                        dead.append(sub)
                        continue
                    sub.sendall(frame)
            except (OSError, ValueError):
                # ValueError: ``select`` rejects a closed fd (-1) by raising
                # ValueError rather than OSError. Treat it the same as a
                # broken socket — the subscriber is gone, drop it.
                dead.append(sub)
        for sub in dead:
            self._drop_subscriber(sub)

    def _drop_subscriber(self, conn: socket.socket) -> None:
        with self._lock:
            self._subscribers.pop(conn, None)
        with suppress(OSError):
            conn.close()
