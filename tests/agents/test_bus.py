"""Tests for the local-host pub/sub agent bus over a Unix-domain socket."""

from __future__ import annotations

import importlib.util
import queue
import socket
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

_POSIX_FCNTL_AVAILABLE = importlib.util.find_spec("fcntl") is not None

from app.agents import bus as bus_module
from app.agents.bus import (
    BUS_SCHEMA_VERSION,
    BusMessage,
    BusServer,
    _pid_file_for,
    _read_broker_pid,
    _socket_is_live,
    publish,
    subscribe,
)


@pytest.fixture
def sock_path(tmp_path: Path) -> Path:
    return tmp_path / "bus.sock"


@pytest.fixture(autouse=True)
def isolated_brokers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with empty broker + publisher caches.

    Without this, the publisher socket cached from a prior test would point at
    a now-stopped broker's socket path, and the first ``publish()`` of the
    next test would silently sendall onto a dead fd.
    """
    monkeypatch.setattr(bus_module, "_brokers", {})
    monkeypatch.setattr(bus_module, "_publishers", {})


def _drain_subscriber(
    sock_path: Path,
    into: queue.Queue[BusMessage],
    stop_after: int = 1,
    *,
    attach_timeout: float = 3.0,
) -> threading.Thread:
    """Spawn a daemon subscriber and block until it has attached to the broker.

    Avoids the flaky ``time.sleep(0.15)`` pattern: we busy-poll the broker's
    in-memory subscriber set until it ticks up by exactly one. Works because
    our process is the broker for the test path (the autouse fixture clears
    the broker registry, so ``subscribe()`` self-elects on first attach).
    """

    def _loop() -> None:
        for count, msg in enumerate(subscribe(path=sock_path), start=1):
            into.put(msg)
            if count >= stop_after:
                return

    # Snapshot the pre-attach subscriber count so we don't race against
    # subscribers spawned by other helpers in the same test.
    broker_before = bus_module._brokers.get(sock_path)
    before = len(broker_before._subscribers) if broker_before is not None else 0

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()

    deadline = time.monotonic() + attach_timeout
    while time.monotonic() < deadline:
        broker = bus_module._brokers.get(sock_path)
        if broker is not None:
            with broker._lock:
                if len(broker._subscribers) > before:
                    return thread
        time.sleep(0.005)
    raise AssertionError(f"_drain_subscriber: subscriber did not attach within {attach_timeout}s")


class TestBusMessage:
    def test_round_trip_preserves_all_fields(self) -> None:
        original = BusMessage(
            agent="claude-code:8421",
            topic="finding",
            summary="null deref on missing token",
            source="github",
            path="services/auth.py:42",
            data={"commit": "abc", "line": 42},
        )
        decoded = BusMessage.from_jsonl(original.to_jsonl())
        assert decoded == original

    def test_to_jsonl_ends_with_newline(self) -> None:
        msg = BusMessage(agent="a:1", topic="finding", summary="x")
        assert msg.to_jsonl().endswith(b"\n")

    def test_from_jsonl_supplies_defaults_for_optional_fields(self) -> None:
        # Minimum required fields only — source/path/data should default.
        wire = b'{"agent":"a:1","topic":"finding","summary":"x"}\n'
        msg = BusMessage.from_jsonl(wire)
        assert msg.source == ""
        assert msg.path == ""
        assert msg.data == {}
        assert msg.schema_version == BUS_SCHEMA_VERSION

    def test_from_jsonl_rejects_non_object_payload(self) -> None:
        with pytest.raises(ValueError):
            BusMessage.from_jsonl(b'["not","an","object"]')

    def test_from_jsonl_rejects_missing_required_field(self) -> None:
        with pytest.raises(KeyError):
            BusMessage.from_jsonl(b'{"agent":"a:1","topic":"finding"}')

    def test_is_not_hashable(self) -> None:
        # ``data`` is a mapping, so a value hash would be misleading and would
        # fail at call time on the auto-generated ``__hash__``. We disable it
        # explicitly. Assert the contract directly (``__hash__ is None``);
        # Python's hash protocol guarantees ``TypeError`` from there, no need
        # to re-test the language (and triggering ``hash()`` here trips
        # CodeQL's "Unhashable object hashed" rule).
        assert BusMessage.__hash__ is None

    def test_data_is_read_only_post_construction(self) -> None:
        msg = BusMessage(agent="a:1", topic="finding", summary="x", data={"k": 1})
        with pytest.raises(TypeError):
            msg.data["k"] = 2  # type: ignore[index]

    def test_data_is_isolated_from_caller_mutation(self) -> None:
        # External mutation of the originally-passed dict must not bleed into
        # the message — defensive copy in ``__post_init__`` enforces this.
        src: dict[str, object] = {"k": 1}
        msg = BusMessage(agent="a:1", topic="finding", summary="x", data=src)
        src["k"] = 999
        assert msg.data["k"] == 1


class TestBusServerLifecycle:
    def test_start_binds_socket_and_stop_unlinks(self, sock_path: Path) -> None:
        server = BusServer(sock_path)
        try:
            server.start()
            assert sock_path.exists()
            assert _socket_is_live(sock_path)
            assert server.is_running
        finally:
            server.stop()
        assert not sock_path.exists()
        assert not server.is_running

    def test_start_is_idempotent(self, sock_path: Path) -> None:
        server = BusServer(sock_path)
        try:
            server.start()
            server.start()  # second call should be a no-op, not raise
            assert server.is_running
        finally:
            server.stop()

    def test_stop_is_idempotent(self, sock_path: Path) -> None:
        server = BusServer(sock_path)
        server.start()
        server.stop()
        server.stop()  # second call should be a no-op, not raise

    def test_start_writes_pid_file_and_stop_removes_it(self, sock_path: Path) -> None:
        import os

        server = BusServer(sock_path)
        server.start()
        try:
            pid_path = _pid_file_for(sock_path)
            assert pid_path.exists()
            assert _read_broker_pid(sock_path) == os.getpid()
        finally:
            server.stop()
        assert not _pid_file_for(sock_path).exists()

    def test_start_rolls_back_when_pid_file_write_fails(
        self, sock_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate ENOSPC / EACCES during the PID-file write. ``start`` must
        # raise *and* leave no orphaned socket file behind — otherwise peers
        # would see a path with no live listener via ``_socket_is_live``,
        # ``_unlink_stale`` it, and silently split the bus.
        def _boom_enospc(*args: object) -> None:
            del args
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(bus_module, "_write_pid_file_atomic", _boom_enospc)

        server = BusServer(sock_path)
        with pytest.raises(OSError):
            server.start()
        assert not sock_path.exists()
        assert not _pid_file_for(sock_path).exists()
        assert not server.is_running

    def test_ensure_broker_propagates_pid_write_failure(
        self, sock_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``_ensure_broker`` swallows EADDRINUSE/EEXIST as a lost bind race,
        # but real failures (disk full, permission denied) must propagate so
        # callers see the error instead of silently splitting the bus.
        def _boom_eacces(*args: object) -> None:
            del args
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(bus_module, "_write_pid_file_atomic", _boom_eacces)
        with pytest.raises(OSError):
            bus_module._ensure_broker(sock_path)


class TestLivenessProbe:
    def test_socket_is_live_does_not_create_phantom_subscriber(self, sock_path: Path) -> None:
        # _socket_is_live used to make a real connection on every probe; under
        # publish/subscribe bursts that registered a short-lived subscriber +
        # reader thread per call. Verify the side-channel probe makes none.
        server = BusServer(sock_path)
        server.start()
        try:
            for _ in range(20):
                assert _socket_is_live(sock_path)
            with server._lock:
                assert len(server._subscribers) == 0
        finally:
            server.stop()

    def test_socket_is_live_false_when_pid_missing(self, sock_path: Path) -> None:
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.touch()  # socket file present but no pid sidecar
        assert not _socket_is_live(sock_path)

    def test_socket_is_live_false_when_pid_dead(self, sock_path: Path) -> None:
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.touch()
        # PID 999999 is almost certainly not a real process. The probe must
        # report not-live so the caller will unlink + rebind.
        _pid_file_for(sock_path).write_text("999999")
        assert not _socket_is_live(sock_path)


class TestPublisherCache:
    def test_burst_of_publishes_reuses_one_connection(self, sock_path: Path) -> None:
        # Each publish() previously opened a fresh UDS connection, which the
        # broker accepted, registered as a subscriber, and ran a per-connection
        # reader thread for. A burst of N publishes spawned N short-lived
        # threads. With the cache, all publishes from one process share one
        # persistent connection and one persistent broker reader thread.
        received: queue.Queue[BusMessage] = queue.Queue()
        _drain_subscriber(sock_path, received, stop_after=20)

        for i in range(20):
            publish(BusMessage(agent="a:1", topic="finding", summary=f"n{i}"), path=sock_path)

        # All frames arrive in order.
        for i in range(20):
            msg = received.get(timeout=2.0)
            assert msg.summary == f"n{i}"

        # Exactly one cached publisher socket for our process.
        assert len(bus_module._publishers) == 1
        cached = next(iter(bus_module._publishers.values()))
        assert cached.sock.fileno() >= 0

        # Broker side: at most one publisher-origin connection (plus the one
        # subscriber drain). Check by counting connections opened by us. The
        # broker only sees what we sent it; if reuse is working, every publish
        # came from the same socket and the broker registered exactly one
        # publisher connection for the burst.
        broker = bus_module._brokers[sock_path]
        with broker._lock:
            # 1 subscriber (the drain thread above) + 1 cached publisher = 2.
            # If the cache were broken we'd see many transient connections,
            # but most would have closed by now — so this is a weak check;
            # the strong check is len(bus_module._publishers) == 1 above.
            assert len(broker._subscribers) <= 2

    def test_reconnects_after_cached_socket_breaks(self, sock_path: Path) -> None:
        received: queue.Queue[BusMessage] = queue.Queue()
        _drain_subscriber(sock_path, received, stop_after=2)

        publish(BusMessage(agent="a:1", topic="finding", summary="first"), path=sock_path)
        msg1 = received.get(timeout=2.0)
        assert msg1.summary == "first"

        # Forcibly break the cached publisher connection.
        broken_cached = next(iter(bus_module._publishers.values()))
        broken_sock = broken_cached.sock
        broken_sock.close()

        # Next publish must transparently reconnect (one retry on OSError).
        publish(BusMessage(agent="a:1", topic="finding", summary="second"), path=sock_path)
        msg2 = received.get(timeout=2.0)
        assert msg2.summary == "second"

        # The cache now holds a fresh socket object (the OS may recycle the
        # underlying fd number, so compare object identity, not fileno()).
        new_cached = next(iter(bus_module._publishers.values()))
        assert new_cached.sock is not broken_sock

    def test_publish_retries_on_initial_connect_failure(
        self, sock_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The first ``_get_or_open_publisher`` raises (e.g. broker exited
        # between liveness check and connect). ``publish`` must run
        # ``_ensure_broker`` and retry once instead of failing immediately.
        real_get = bus_module._get_or_open_publisher
        calls = {"n": 0}

        def _flaky(target: Path, *, connect_timeout: float) -> bus_module._CachedPublisher:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionRefusedError(111, "Connection refused")
            return real_get(target, connect_timeout=connect_timeout)

        monkeypatch.setattr(bus_module, "_get_or_open_publisher", _flaky)

        received: queue.Queue[BusMessage] = queue.Queue()
        _drain_subscriber(sock_path, received, stop_after=1)

        publish(BusMessage(agent="a:1", topic="finding", summary="retried"), path=sock_path)
        msg = received.get(timeout=2.0)
        assert msg.summary == "retried"
        assert calls["n"] == 2, "publish() did not retry on initial connect failure"

    def test_subscribe_retries_on_initial_connect_failure(
        self, sock_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``subscribe`` previously raised on the first connect failure;
        # symmetric with ``publish``, it now retries once.
        real_connect = bus_module._connect_client
        calls = {"n": 0}

        def _flaky(target: Path, timeout: float) -> socket.socket:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionRefusedError(111, "Connection refused")
            return real_connect(target, timeout=timeout)

        monkeypatch.setattr(bus_module, "_connect_client", _flaky)

        # Just calling subscribe() and pulling the first batch is enough —
        # if the retry didn't happen, the call would raise.
        received: queue.Queue[BusMessage] = queue.Queue()

        def _loop() -> None:
            for msg in subscribe(path=sock_path):
                received.put(msg)
                return

        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()

        # Wait for subscribe() to attach, give up to 2s.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            broker = bus_module._brokers.get(sock_path)
            if broker is not None:
                with broker._lock:
                    if broker._subscribers:
                        break
            time.sleep(0.005)
        broker = bus_module._brokers.get(sock_path)
        assert broker is not None and broker._subscribers, "subscriber never attached after retry"
        assert calls["n"] >= 2, "subscribe() did not retry on initial connect failure"

    def test_concurrent_publishes_do_not_interleave_frames(self, sock_path: Path) -> None:
        # If the per-socket send_lock weren't held around sendall(), concurrent
        # publishes from different threads could interleave bytes mid-frame,
        # corrupting the JSONL stream.
        n_threads = 10
        per_thread = 5
        total = n_threads * per_thread

        received: queue.Queue[BusMessage] = queue.Queue()
        _drain_subscriber(sock_path, received, stop_after=total)

        def _spam(tid: int) -> None:
            for i in range(per_thread):
                publish(
                    BusMessage(
                        agent=f"t{tid}:1",
                        topic="finding",
                        summary=f"t{tid}-n{i}",
                    ),
                    path=sock_path,
                )

        threads = [threading.Thread(target=_spam, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Every frame must be parseable (no byte interleaving) and unique.
        seen: set[str] = set()
        for _ in range(total):
            msg = received.get(timeout=5.0)
            seen.add(msg.summary)
        assert len(seen) == total, f"frame loss or corruption: got {len(seen)} unique of {total}"


class TestPublishSubscribe:
    def test_round_trip_one_publisher_one_subscriber(self, sock_path: Path) -> None:
        received: queue.Queue[BusMessage] = queue.Queue()
        _drain_subscriber(sock_path, received)

        publish(
            BusMessage(
                agent="claude-code:8421",
                topic="finding",
                summary="null deref",
                path="services/auth.py:42",
            ),
            path=sock_path,
        )

        msg = received.get(timeout=2.0)
        assert msg.agent == "claude-code:8421"
        assert msg.summary == "null deref"
        assert msg.path == "services/auth.py:42"

    def test_one_publisher_multiple_subscribers_all_receive(self, sock_path: Path) -> None:
        n_subs = 3
        sub_queues: list[queue.Queue[BusMessage]] = [queue.Queue() for _ in range(n_subs)]
        for q in sub_queues:
            _drain_subscriber(sock_path, q)

        publish(BusMessage(agent="a:1", topic="finding", summary="hello"), path=sock_path)

        for q in sub_queues:
            msg = q.get(timeout=2.0)
            assert msg.summary == "hello"

    def test_broadcast_holds_per_subscriber_write_lock(self, sock_path: Path) -> None:
        # The bug: two reader-threads broadcasting concurrently to the same
        # subscriber socket can interleave bytes mid-frame because
        # ``sendall`` is multi-syscall under back-pressure. The fix is a
        # per-subscriber write lock acquired around ``select`` + ``sendall``
        # in ``_broadcast``.
        #
        # Reliably reproducing kernel-level byte interleaving across
        # systems is fragile (depends on SNDBUF/RCVBUF tuning). Test the
        # lock contract directly: hold the per-subscriber write_lock
        # externally and confirm ``_broadcast`` blocks until release.
        server = BusServer(sock_path)
        server.start()
        sub: socket.socket | None = None
        try:
            sub = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sub.connect(str(sock_path))

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                with server._lock:
                    if server._subscribers:
                        break
                time.sleep(0.005)
            with server._lock:
                assert server._subscribers, "subscriber never attached"
                # Grab the broker-side (sub, write_lock) pair.
                ((_broker_sub, write_lock),) = server._subscribers.items()

            # Hold the lock as if another reader-thread were mid-sendall.
            assert write_lock.acquire(timeout=1.0)
            broadcast_returned = threading.Event()
            try:

                def _bcast() -> None:
                    server._broadcast(b'{"x":"y"}\n', origin=None)
                    broadcast_returned.set()

                t = threading.Thread(target=_bcast, daemon=True)
                t.start()

                # While we hold the lock, ``_broadcast`` must wait — without
                # the lock it would race straight into ``sendall``.
                assert not broadcast_returned.wait(timeout=0.2), (
                    "broadcast did not block on per-subscriber write lock"
                )
            finally:
                write_lock.release()

            # After release, broadcast should complete promptly.
            assert broadcast_returned.wait(timeout=2.0), (
                "broadcast did not return after lock release"
            )
        finally:
            if sub is not None:
                with suppress(OSError):
                    sub.close()
            server.stop()

    def test_publisher_does_not_receive_own_frame(self, sock_path: Path) -> None:
        # Self-elect a broker so we can attach as a single client that
        # both publishes and subscribes — and verify the broadcaster
        # does not echo the frame back to its origin.
        bus_module._ensure_broker(sock_path)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(sock_path))
        client.settimeout(0.5)
        try:
            client.sendall(BusMessage(agent="a:1", topic="finding", summary="x").to_jsonl())
            with pytest.raises(socket.timeout):
                client.recv(4096)
        finally:
            client.close()

    def test_subscribe_skips_malformed_frames(self, sock_path: Path) -> None:
        received: queue.Queue[BusMessage] = queue.Queue()
        _drain_subscriber(sock_path, received)

        # Connect a raw publisher and inject a malformed frame followed by a valid one.
        bus_module._ensure_broker(sock_path)
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.connect(str(sock_path))
        try:
            raw.sendall(b"this-is-not-json\n")
            raw.sendall(BusMessage(agent="a:1", topic="finding", summary="ok").to_jsonl())
        finally:
            # Keep the publisher alive briefly so the broker can forward both frames.
            time.sleep(0.1)
            raw.close()

        msg = received.get(timeout=2.0)
        assert msg.summary == "ok"

    def test_unresponsive_subscriber_does_not_stall_broadcast(
        self, sock_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If one subscriber's recv buffer fills, ``sendall`` on a blocking UDS
        # would wedge indefinitely with no exception, freezing fan-out for
        # every publisher. Verify the write-readiness gate evicts the slow
        # subscriber and lets healthy ones keep receiving.
        monkeypatch.setattr(bus_module, "_BROADCAST_WRITE_TIMEOUT_SECONDS", 0.05)

        server = BusServer(sock_path)
        server.start()
        slow: socket.socket | None = None
        try:
            # Healthy subscriber: drains until it sees the "alive" sentinel.
            seen_alive = threading.Event()

            def _healthy_drain() -> None:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(sock_path))
                client.settimeout(3.0)
                buf = b""
                try:
                    while not seen_alive.is_set():
                        chunk = client.recv(8192)
                        if not chunk:
                            return
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            if not line:
                                continue
                            try:
                                msg = BusMessage.from_jsonl(line)
                            except (ValueError, KeyError, TypeError):
                                continue
                            if msg.summary == "alive":
                                seen_alive.set()
                                return
                except OSError:
                    return
                finally:
                    client.close()

            t = threading.Thread(target=_healthy_drain, daemon=True)
            t.start()
            # Slow subscriber: shrink recv buffer so it fills fast, never reads.
            slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            slow.connect(str(sock_path))

            # Wait for both to attach.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                with server._lock:
                    if len(server._subscribers) >= 2:
                        break
                time.sleep(0.02)

            # Pump enough large frames to fill ``slow``'s tiny recv buffer.
            for _ in range(8):
                publish(
                    BusMessage(agent="a:1", topic="finding", summary="x" * 4000),
                    path=sock_path,
                )

            # Final small frame. Healthy subscriber must receive it — that
            # proves the broker did not stall behind the wedged ``slow``.
            publish(BusMessage(agent="a:1", topic="finding", summary="alive"), path=sock_path)

            assert seen_alive.wait(timeout=3.0), (
                "broker stalled: healthy subscriber never received the post-fill frame"
            )

            # Slow subscriber should have been evicted from the fan-out set.
            deadline = time.time() + 1.0
            slow_fd = slow.fileno()
            while time.time() < deadline:
                with server._lock:
                    still_attached = any(s.fileno() == slow_fd for s in server._subscribers)
                if not still_attached:
                    break
                time.sleep(0.05)
            with server._lock:
                attached_fds = {s.fileno() for s in server._subscribers}
            assert slow_fd not in attached_fds, (
                "unresponsive subscriber was not evicted from fan-out set"
            )
        finally:
            if slow is not None:
                slow.close()
            server.stop()

    def test_subscriber_disconnects_on_oversized_unterminated_stream(self, sock_path: Path) -> None:
        # Simulate a hostile broker that wins the bind race and streams unlimited
        # bytes without newlines. The subscriber must cap its buffer and bail
        # rather than grow memory unboundedly.
        from app.agents.bus import _MAX_FRAME_BYTES, BusServer, subscribe

        # Stand up a fake "hostile" broker that pushes garbage to every client.
        server = BusServer(sock_path)
        server.start()
        try:
            done = threading.Event()
            error: list[Exception] = []

            def _consume() -> None:
                try:
                    # Drain until subscribe() returns (which it should, on cap breach).
                    for _ in subscribe(path=sock_path):
                        pass
                except Exception as exc:
                    error.append(exc)
                finally:
                    done.set()

            t = threading.Thread(target=_consume, daemon=True)
            t.start()
            # Wait for the subscriber to attach to the broker.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                with server._lock:
                    if server._subscribers:
                        break
                time.sleep(0.02)
            assert server._subscribers, "subscriber never attached"

            # Push a single oversized chunk through to all subscribers.
            payload = b"X" * (_MAX_FRAME_BYTES * 4 + 1024)
            with server._lock:
                victim = next(iter(server._subscribers))
            victim.sendall(payload)

            # Subscriber should disconnect on its own without raising.
            assert done.wait(timeout=2.0), "subscriber did not disconnect on cap breach"
            assert not error, f"subscribe() raised unexpectedly: {error}"
        finally:
            server.stop()


class TestBrokerElectionRace:
    def test_concurrent_cold_start_election_does_not_orphan_a_broker(self, sock_path: Path) -> None:
        # Cold-start race: two processes both observe ``_socket_is_live``
        # → False, both call ``_unlink_stale``, both try to bind. The
        # kernel only lets one bind succeed, but without cross-process
        # serialization the loser is left holding a listener fd whose
        # filesystem path the winner just unlinked, plus the accept/
        # reader daemon threads. The election ``flock`` prevents that.
        #
        # We simulate the race by holding the flock from this test
        # process and concurrently spawning a child that calls
        # ``_ensure_broker``. The child must block on the flock until we
        # release it, then return ``None`` (we elected first).
        import os
        import subprocess
        import sys
        import textwrap

        # Bind a broker first; once it's live, any peer (including the
        # subprocess we'll spawn) sees it via the PID-file side channel and
        # backs off. The flock only matters during the check-unlink-bind
        # window, which closes after ``server.start()`` returns.
        server = bus_module.BusServer(sock_path)
        server.start()
        try:
            assert sock_path.exists()
            assert _pid_file_for(sock_path).exists()

            # Spawn a real subprocess that calls ``_ensure_broker`` for
            # the same path. With the election lock and the live PID
            # sidecar, the child must observe our broker and return
            # ``None``. Without the flock contract, a concurrent cold
            # start could unlink our socket file and rebind, orphaning
            # this server.
            repo_root = Path(__file__).resolve().parents[2]
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    textwrap.dedent(
                        f"""
                        from pathlib import Path
                        from app.agents import bus

                        result = bus._ensure_broker(Path({str(sock_path)!r}))
                        print("OWNER" if result is not None else "PEER")
                        """
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=10.0,
                env={**os.environ, "PYTHONPATH": str(repo_root)},
            )
            assert child.returncode == 0, child.stderr
            assert child.stdout.strip() == "PEER", (
                f"child wrongly elected itself: stdout={child.stdout!r} stderr={child.stderr!r}"
            )

            # Our broker is unscathed: socket file + pid file still there,
            # listener still accepting connections.
            assert sock_path.exists(), "winner's socket file vanished"
            assert _pid_file_for(sock_path).exists(), "winner's pid file vanished"
            assert server.is_running
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            client.connect(str(sock_path))
            client.close()
        finally:
            server.stop()

    def test_election_lock_path_lives_next_to_socket(self, sock_path: Path) -> None:
        # Sanity-check the sidecar location so future refactors don't
        # silently move it (which would re-open the cross-process race).
        lock_path = bus_module._election_lock_path(sock_path)
        assert lock_path.parent == sock_path.parent
        assert lock_path.name == sock_path.name + ".lock"

    @pytest.mark.skipif(
        not _POSIX_FCNTL_AVAILABLE,
        reason="cross-process election flock requires POSIX fcntl (omitted on Windows)",
    )
    def test_ensure_broker_blocks_on_election_flock_held_by_peer(self, sock_path: Path) -> None:
        # Direct test of the cross-process serialization: a child process
        # holds the election flock; this process's ``_ensure_broker``
        # must block on ``flock(LOCK_EX)`` until the child releases it.
        # Without this, two concurrent cold-start callers can race
        # through unlink+bind and leave one orphaned.
        import os
        import subprocess
        import sys
        import textwrap

        repo_root = Path(__file__).resolve().parents[2]
        # Ensure parent dir exists so the child can open the lock file.
        sock_path.parent.mkdir(parents=True, exist_ok=True)

        # Child: open + flock the election file, signal "READY", sleep,
        # then exit (flock auto-released on close).
        child = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                textwrap.dedent(
                    f"""
                    import os, fcntl, sys, time
                    from pathlib import Path
                    from app.agents import bus

                    fd = bus._acquire_election_flock(Path({str(sock_path)!r}))
                    print("READY", flush=True)
                    time.sleep(0.5)
                    bus._release_election_flock(fd)
                    """
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(repo_root)},
        )
        try:
            # Wait for the child to confirm it holds the lock.
            assert child.stdout is not None
            line = child.stdout.readline()
            assert line.strip() == "READY", f"child did not signal ready: {line!r}"

            # Now ``_ensure_broker`` here must wait on the flock. Time
            # the call: it should take roughly the remaining sleep time.
            t0 = time.monotonic()
            server = bus_module._ensure_broker(sock_path)
            elapsed = time.monotonic() - t0
            try:
                assert server is not None, "we should have elected after child released"
                # The child slept 0.5s total starting before READY; we
                # should have waited for some appreciable fraction of
                # that. 0.1s is well under the 0.5s sleep but well above
                # zero, so it cleanly distinguishes "blocked on flock"
                # from "didn't block at all".
                assert elapsed >= 0.1, (
                    f"_ensure_broker did not block on the election flock (elapsed={elapsed:.3f}s)"
                )
            finally:
                if server is not None:
                    server.stop()
        finally:
            child.wait(timeout=5.0)


class TestBrokerSelfElection:
    def test_stale_socket_file_is_unlinked_and_rebound(self, sock_path: Path) -> None:
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.touch()  # stale: no listener
        assert sock_path.exists() and not _socket_is_live(sock_path)

        server = bus_module._ensure_broker(sock_path)
        try:
            assert server is not None
            assert server.is_running
            assert _socket_is_live(sock_path)
        finally:
            if server is not None:
                server.stop()

    def test_ensure_broker_returns_none_when_other_process_owns_it(self, sock_path: Path) -> None:
        # Stand up an "other" broker via direct BusServer (simulating another process),
        # then call _ensure_broker — it should detect the live socket and back off.
        other = BusServer(sock_path)
        other.start()
        try:
            # Pretend our process has not yet elected: empty the registry.
            bus_module._brokers.clear()
            result = bus_module._ensure_broker(sock_path)
            assert result is None
        finally:
            other.stop()

    def test_ensure_broker_idempotent_for_same_process(self, sock_path: Path) -> None:
        first = bus_module._ensure_broker(sock_path)
        second = bus_module._ensure_broker(sock_path)
        try:
            assert first is not None
            assert first is second
        finally:
            if first is not None:
                first.stop()


class TestSlashCommandFormatter:
    def test_format_includes_agent_and_summary(self) -> None:
        from app.cli.interactive_shell.command_registry.agents import _format_bus_message

        msg = BusMessage(agent="claude-code:8421", topic="finding", summary="hi")
        out = _format_bus_message(msg)
        assert "claude-code:8421" in out
        assert "hi" in out

    def test_format_includes_path_when_present(self) -> None:
        from app.cli.interactive_shell.command_registry.agents import _format_bus_message

        msg = BusMessage(agent="a:1", topic="finding", summary="x", path="services/auth.py:42")
        out = _format_bus_message(msg)
        assert "services/auth.py:42" in out
        assert "—" in out

    def test_format_omits_separator_when_no_path(self) -> None:
        from app.cli.interactive_shell.command_registry.agents import _format_bus_message

        msg = BusMessage(agent="a:1", topic="finding", summary="x")
        out = _format_bus_message(msg)
        assert "—" not in out
