"""Tests for ``app/agents/network_egress.py`` (issue #1500).

Mirrors the structure of ``test_sudo_invocations.py`` — the two
watchers are deliberately twins of the same shape so the test
suites are uniform.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import psutil
import pytest

from app.agents import network_egress as egress_module
from app.agents.network_egress import (
    NetworkEgressEvent,
    NetworkEgressWatcher,
    _reset_watchers_for_tests,
    collect_recent_egress_events,
)
from app.agents.registry import AgentRecord


@pytest.fixture(autouse=True)
def _reset_watcher_cache() -> Iterator[None]:
    """Reset module-global watcher cache before and after each test."""
    _reset_watchers_for_tests()
    yield
    _reset_watchers_for_tests()


def _record(name: str = "claude-code", pid: int = 4242) -> AgentRecord:
    return AgentRecord(name=name, pid=pid, command="claude")


class _FakeAddr:
    def __init__(self, ip: str, port: int) -> None:
        self.ip = ip
        self.port = port


class _FakeConn:
    """Minimal stand-in for a ``psutil._Connection`` namedtuple-like."""

    def __init__(
        self,
        *,
        status: str,
        raddr: _FakeAddr | tuple[()] | None,
        family: int = socket.AF_INET,
    ) -> None:
        self.status = status
        self.raddr = raddr
        self.family = family


class _FakeProcess:
    """Stand-in for ``psutil.Process(pid)`` used by the egress watcher."""

    def __init__(self, connections: list[_FakeConn]) -> None:
        self._connections = connections

    def net_connections(self, kind: str = "inet") -> list[_FakeConn]:  # noqa: ARG002
        return self._connections


class TestNetworkEgressEventDataclass:
    def test_is_frozen_and_hashable(self) -> None:
        e = NetworkEgressEvent(
            agent="a:1", remote_host="1.2.3.4", remote_port=443, family="ipv4", timestamp=1.0
        )
        assert e in {e}
        with pytest.raises(AttributeError):
            e.remote_host = "5.6.7.8"  # type: ignore[misc]


class TestNetworkEgressWatcherPolling:
    def _watcher(self) -> NetworkEgressWatcher:
        return NetworkEgressWatcher(_record(), poll_interval=10.0)

    def test_records_first_contact_with_a_remote_host(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [_FakeConn(status=psutil.CONN_ESTABLISHED, raddr=_FakeAddr("1.2.3.4", 443))]
        )
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        events = watcher.events()
        assert len(events) == 1
        assert events[0].remote_host == "1.2.3.4"
        assert events[0].remote_port == 443
        assert events[0].family == "ipv4"
        assert events[0].agent == "claude-code:4242"

    def test_dedupes_same_destination_across_polls(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [_FakeConn(status=psutil.CONN_ESTABLISHED, raddr=_FakeAddr("1.2.3.4", 443))]
        )
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
            watcher._poll_once()
        assert len(watcher.events()) == 1

    def test_distinct_ports_on_same_host_are_separate_events(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [
                _FakeConn(status=psutil.CONN_ESTABLISHED, raddr=_FakeAddr("1.2.3.4", 443)),
                _FakeConn(status=psutil.CONN_ESTABLISHED, raddr=_FakeAddr("1.2.3.4", 80)),
            ]
        )
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        ports = {e.remote_port for e in watcher.events()}
        assert ports == {443, 80}

    def test_listen_and_close_are_filtered_out(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [
                _FakeConn(status=psutil.CONN_LISTEN, raddr=_FakeAddr("0.0.0.0", 8080)),
                _FakeConn(status=psutil.CONN_NONE, raddr=()),
                _FakeConn(status=psutil.CONN_CLOSE, raddr=_FakeAddr("8.8.8.8", 53)),
            ]
        )
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        assert watcher.events() == []

    def test_skips_connections_with_no_remote_address(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess([_FakeConn(status=psutil.CONN_ESTABLISHED, raddr=())])
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        assert watcher.events() == []

    def test_ipv6_family_is_recorded(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [
                _FakeConn(
                    status=psutil.CONN_ESTABLISHED,
                    raddr=_FakeAddr("2606:4700::1111", 443),
                    family=socket.AF_INET6,
                )
            ]
        )
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        events = watcher.events()
        assert len(events) == 1
        assert events[0].family == "ipv6"

    def test_swallows_psutil_errors(self) -> None:
        watcher = self._watcher()
        with patch(
            "app.agents.network_egress.psutil.Process",
            side_effect=psutil.NoSuchProcess(pid=4242),
        ):
            watcher._poll_once()
        assert watcher.events() == []

    def test_events_since_filters_by_timestamp(self) -> None:
        watcher = self._watcher()
        watcher._events.append(
            NetworkEgressEvent(
                agent="a:1", remote_host="1.1.1.1", remote_port=80, family="ipv4", timestamp=10.0
            )
        )
        watcher._events.append(
            NetworkEgressEvent(
                agent="a:1", remote_host="2.2.2.2", remote_port=443, family="ipv4", timestamp=30.0
            )
        )
        out = watcher.events_since(since=20.0)
        assert [e.remote_host for e in out] == ["2.2.2.2"]


class TestNetworkEgressWatcherLifecycle:
    def test_start_and_stop_are_idempotent(self) -> None:
        watcher = NetworkEgressWatcher(_record(), poll_interval=0.01)
        with patch(
            "app.agents.network_egress.psutil.Process",
            side_effect=psutil.NoSuchProcess(pid=4242),
        ):
            try:
                watcher.start()
                watcher.start()
            finally:
                watcher.stop()
                watcher.stop()

    def test_thread_actually_polls(self) -> None:
        watcher = NetworkEgressWatcher(_record(), poll_interval=0.01)
        fake_proc = _FakeProcess(
            [_FakeConn(status=psutil.CONN_ESTABLISHED, raddr=_FakeAddr("1.2.3.4", 443))]
        )
        with patch("app.agents.network_egress.psutil.Process", return_value=fake_proc):
            watcher.start()
            deadline = time.time() + 1.5
            while time.time() < deadline and not watcher.events():
                time.sleep(0.05)
            try:
                assert watcher.events()
            finally:
                watcher.stop()


class TestCollectRecentEgressEvents:
    def test_lazy_start_aggregates_across_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(egress_module, "_agent_pid_exists", lambda _: True)
        started: list[str] = []

        class _StubWatcher:
            def __init__(self, record: AgentRecord, **_kwargs: Any) -> None:
                self._record = record

            def start(self) -> None:
                started.append(f"{self._record.name}:{self._record.pid}")

            def stop(self) -> None:
                pass

            def events_since(self, _since: float) -> list[NetworkEgressEvent]:
                return [
                    NetworkEgressEvent(
                        agent=f"{self._record.name}:{self._record.pid}",
                        remote_host="1.2.3.4",
                        remote_port=443,
                        family="ipv4",
                        timestamp=100.0,
                    )
                ]

        monkeypatch.setattr(egress_module, "NetworkEgressWatcher", _StubWatcher)

        records = [_record(name="a1", pid=1), _record(name="a2", pid=2)]
        out = collect_recent_egress_events(records, since=0.0)
        assert {e.agent for e in out} == {"a1:1", "a2:2"}
        assert sorted(started) == ["a1:1", "a2:2"]

    def test_unresolvable_pid_is_negative_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def fake_exists(record: AgentRecord) -> bool:
            calls.append(record.pid)
            return False

        monkeypatch.setattr(egress_module, "_agent_pid_exists", fake_exists)
        records = [_record(pid=99999)]
        collect_recent_egress_events(records, since=0.0)
        collect_recent_egress_events(records, since=0.0)
        assert calls == [99999]
        assert "claude-code:99999" in egress_module._UNRESOLVABLE


class TestEventsLockSerialization:
    def test_concurrent_append_and_iter_does_not_raise(self) -> None:
        watcher = NetworkEgressWatcher(_record(), poll_interval=10.0)
        stop = threading.Event()

        def appender() -> None:
            i = 0
            while not stop.is_set():
                with watcher._events_lock:
                    watcher._events.append(
                        NetworkEgressEvent(
                            agent="a:1",
                            remote_host=f"10.0.0.{i % 256}",
                            remote_port=443,
                            family="ipv4",
                            timestamp=float(i),
                        )
                    )
                i += 1

        t = threading.Thread(target=appender, daemon=True)
        t.start()
        try:
            for _ in range(200):
                watcher.events()
        finally:
            stop.set()
            t.join(timeout=1.0)
