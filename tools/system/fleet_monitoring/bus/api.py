"""Public entrypoints for publishing to and subscribing from the agent bus."""

from __future__ import annotations

import json
import logging
import socket
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

from . import election, publisher_pool
from .message import BusMessage
from .server import _MAX_FRAME_BYTES

logger = logging.getLogger(__name__)

DEFAULT_BUS_SOCKET_PATH: Path = OPENSRE_HOME_DIR / "agents-bus.sock"


def publish(
    message: BusMessage,
    *,
    path: Path | None = None,
    connect_timeout: float = 1.0,
) -> None:
    """Publish ``message`` to every current subscriber on the bus.

    Self-elects a broker if none is running. Send is fire-and-forget: if no
    subscribers are attached, the frame is dropped by the broker (live-only,
    no replay buffer in v1).

    Publisher sockets are cached per ``path`` and reused across calls so a
    burst of publishes does not spawn one broker reader-thread per call. On
    any transient ``OSError`` — failed initial connect, broken cached
    connection, or send error — one retry is attempted (re-electing the
    broker if needed) before propagating the error.
    """
    target = path or DEFAULT_BUS_SOCKET_PATH
    election._ensure_broker(target)
    frame = message.to_jsonl()
    last_err: OSError | None = None
    for attempt in range(2):
        cached: publisher_pool._CachedPublisher | None = None
        try:
            cached = publisher_pool._get_or_open_publisher(target, connect_timeout=connect_timeout)
            with cached.send_lock:
                cached.sock.sendall(frame)
            return
        except OSError as exc:
            last_err = exc
            if cached is not None:
                publisher_pool._drop_publisher(target, cached.sock)
            if attempt == 0:
                election._ensure_broker(target)
    assert last_err is not None
    raise last_err


def subscribe(
    *,
    path: Path | None = None,
    connect_timeout: float = 1.0,
) -> Iterator[BusMessage]:
    """Yield ``BusMessage``s as they arrive on the bus until the broker disconnects.

    Self-elects a broker if none is running, then attaches as a subscriber and
    streams frames. Malformed lines are logged at WARNING and skipped — one
    misbehaving publisher should not kill an inspector REPL. The iterator ends
    cleanly on broker disconnect; ``KeyboardInterrupt`` propagates so callers
    (e.g. ``/fleet bus``) can return to their prompt.

    A buffer cap mirrors the broker's ``_reader_loop`` guard: any process that
    can ``bind()`` the socket first (filesystem perms are the only auth) could
    otherwise stream unlimited bytes without newlines and exhaust subscriber
    memory. On overflow the subscriber logs a warning and disconnects.

    Initial connect failures are retried once (mirroring ``publish()``) — the
    most common cause is a broker that just exited, in which case
    ``_ensure_broker`` will re-elect on the second pass.
    """
    target = path or DEFAULT_BUS_SOCKET_PATH
    last_connect_err: OSError | None = None
    client: socket.socket | None = None
    for _attempt in range(2):
        election._ensure_broker(target)
        try:
            client = publisher_pool._connect_client(target, timeout=connect_timeout)
            break
        except OSError as exc:
            last_connect_err = exc
    if client is None:
        assert last_connect_err is not None
        raise last_connect_err
    buf = b""
    try:
        while True:
            try:
                chunk = client.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            if len(buf) > _MAX_FRAME_BYTES * 4:
                logger.warning(
                    "bus broker exceeded subscriber buffer cap (%d bytes); disconnecting",
                    len(buf),
                )
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue
                if len(line) > _MAX_FRAME_BYTES:
                    logger.warning("dropping oversized bus frame (%d bytes)", len(line))
                    continue
                try:
                    yield BusMessage.from_jsonl(line)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    logger.warning("dropping malformed bus frame: %s", line[:80])
    finally:
        with suppress(OSError):
            client.close()


__all__ = [
    "DEFAULT_BUS_SOCKET_PATH",
    "publish",
    "subscribe",
]
