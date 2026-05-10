"""Tests for ``app/agents/sudo_invocations.py`` (issue #1500).

Covers four layers of the sudo watcher:

1. ``SudoInvocationEvent`` dataclass — frozen, hashable, identity
2. ``SudoInvocationWatcher._poll_once`` — driven by a stub
   ``psutil.Process`` that returns synthesized child processes; no
   real PIDs needed
3. ``SudoInvocationWatcher`` lifecycle — idempotent start/stop, lock
   serialization between the polling thread and the main-thread
   reader
4. ``collect_recent_sudo_events`` — lazy coordinator with the
   process-global watcher cache and negative cache for missing PIDs

The tests stub ``psutil.Process`` per-test rather than spawning real
``sudo`` subprocesses; the static containment scan in
``test_probe.py`` already enforces the allowlist boundary.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import psutil
import pytest

from app.agents import sudo_invocations as sudo_module
from app.agents.registry import AgentRecord
from app.agents.sudo_invocations import (
    SudoInvocationEvent,
    SudoInvocationWatcher,
    _reset_watchers_for_tests,
    collect_recent_sudo_events,
)


@pytest.fixture(autouse=True)
def _reset_watcher_cache() -> Iterator[None]:
    """Reset the module-global watcher cache before and after each test."""
    _reset_watchers_for_tests()
    yield
    _reset_watchers_for_tests()


def _record(name: str = "claude-code", pid: int = 4242) -> AgentRecord:
    return AgentRecord(name=name, pid=pid, command="claude")


class _FakeChild:
    """Minimal stand-in for ``psutil.Process`` returned by ``children()``."""

    def __init__(
        self,
        pid: int,
        cmdline: list[str],
        *,
        cmdline_raises: type[BaseException] | None = None,
    ) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self._cmdline_raises = cmdline_raises

    def cmdline(self) -> list[str]:
        if self._cmdline_raises is not None:
            raise (
                self._cmdline_raises(  # type: ignore[call-arg]
                    pid=self.pid,
                )
                if self._cmdline_raises is psutil.NoSuchProcess
                else self._cmdline_raises()
            )
        return self._cmdline


class _FakeProcess:
    """Stand-in for ``psutil.Process(pid)`` used by the poller.

    The watcher only calls ``.children(recursive=True)``; everything
    else (init, attrs) is unused, so this minimal shape is enough to
    drive the polling logic without touching real processes.
    """

    def __init__(self, children: list[_FakeChild]) -> None:
        self._children = children

    def children(self, recursive: bool = False) -> list[_FakeChild]:  # noqa: ARG002
        return self._children


class TestSudoInvocationEventDataclass:
    def test_is_frozen_and_hashable(self) -> None:
        e = SudoInvocationEvent(
            agent="claude-code:1", command="sudo apt update", child_pid=99, timestamp=10.0
        )
        assert e in {e}
        with pytest.raises(AttributeError):
            e.command = "x"  # type: ignore[misc]


class TestSudoInvocationWatcherPolling:
    """Drives ``_poll_once`` directly with a stub ``psutil.Process``."""

    def _watcher(self) -> SudoInvocationWatcher:
        return SudoInvocationWatcher(_record(), poll_interval=10.0)

    def test_emits_event_for_sudo_child(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess([_FakeChild(101, ["/usr/bin/sudo", "apt", "update"])])
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        events = watcher.events()
        assert len(events) == 1
        assert events[0].agent == "claude-code:4242"
        assert events[0].child_pid == 101
        assert events[0].command == "/usr/bin/sudo apt update"

    def test_dedupes_repeat_polls_for_same_child_pid(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess([_FakeChild(101, ["/usr/bin/sudo", "ls"])])
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
            watcher._poll_once()
            watcher._poll_once()
        # Same PID across three polls — only one event emitted.
        assert len(watcher.events()) == 1

    def test_matches_doas_and_sudoedit(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [
                _FakeChild(101, ["doas", "shutdown", "now"]),
                _FakeChild(102, ["/usr/bin/sudoedit", "/etc/hosts"]),
            ]
        )
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        commands = {e.command for e in watcher.events()}
        assert commands == {"doas shutdown now", "/usr/bin/sudoedit /etc/hosts"}

    def test_ignores_non_sudo_children(self) -> None:
        watcher = self._watcher()
        fake_proc = _FakeProcess(
            [
                _FakeChild(101, ["python", "-c", "print(1)"]),
                _FakeChild(102, ["/usr/local/bin/pseudo", "-x"]),  # ``pseudo`` != ``sudo``
                _FakeChild(103, ["bash"]),
            ]
        )
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        assert watcher.events() == []

    def test_skips_children_with_empty_cmdline(self) -> None:
        # Kernel threads and zombies report empty cmdline; the watcher
        # must not crash on them.
        watcher = self._watcher()
        fake_proc = _FakeProcess([_FakeChild(101, [])])
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        assert watcher.events() == []

    def test_handles_child_race_during_cmdline_call(self) -> None:
        # Child can exit between ``children()`` and ``cmdline()``;
        # the watcher must skip it without crashing.
        watcher = self._watcher()
        fake_proc = _FakeProcess([_FakeChild(101, [], cmdline_raises=psutil.NoSuchProcess)])
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher._poll_once()
        assert watcher.events() == []

    def test_swallows_psutil_errors_for_agent_pid(self) -> None:
        # Agent itself exits or becomes inaccessible — poll must
        # silently no-op rather than tear the polling thread.
        watcher = self._watcher()
        with patch(
            "app.agents.sudo_invocations.psutil.Process",
            side_effect=psutil.NoSuchProcess(pid=4242),
        ):
            watcher._poll_once()
        assert watcher.events() == []

    def test_seen_pids_is_co_bounded_with_events_deque(self) -> None:
        # Greptile P1: ``_seen_pids`` must shrink in lockstep with
        # ``_events`` so the dedup set can't grow without bound for
        # the lifetime of the REPL session. With ``max_events=3`` and
        # four distinct sudo children, the oldest PID must drop out of
        # both the deque AND the set; if that PID later reappears it
        # should emit a fresh event (proving the dedup memory was
        # reclaimed).
        watcher = SudoInvocationWatcher(_record(), poll_interval=10.0, max_events=3)
        first_round = _FakeProcess(
            [
                _FakeChild(101, ["sudo", "a"]),
                _FakeChild(102, ["sudo", "b"]),
                _FakeChild(103, ["sudo", "c"]),
                _FakeChild(104, ["sudo", "d"]),
            ]
        )
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=first_round):
            watcher._poll_once()
        # Deque caps at 3; oldest (pid 101) was evicted.
        assert len(watcher.events()) == 3
        assert 101 not in watcher._seen_pids
        assert len(watcher._seen_pids) == 3

        # If the same PID is observed again later (PID reuse / new sudo
        # under the freed PID), it must emit a fresh event because the
        # dedup memory has been reclaimed.
        replay = _FakeProcess([_FakeChild(101, ["sudo", "again"])])
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=replay):
            watcher._poll_once()
        assert len(watcher.events()) == 3
        assert len(watcher._seen_pids) == 3
        assert 101 in watcher._seen_pids

    def test_events_since_filters_by_timestamp(self) -> None:
        watcher = self._watcher()
        # Hand-inject events to avoid timing flake on the real clock.
        watcher._events.append(
            SudoInvocationEvent(agent="a:1", command="sudo a", child_pid=1, timestamp=10.0)
        )
        watcher._events.append(
            SudoInvocationEvent(agent="a:1", command="sudo b", child_pid=2, timestamp=20.0)
        )
        watcher._events.append(
            SudoInvocationEvent(agent="a:1", command="sudo c", child_pid=3, timestamp=30.0)
        )
        out = watcher.events_since(since=20.0)
        assert [e.command for e in out] == ["sudo b", "sudo c"]


class TestSudoInvocationWatcherLifecycle:
    def test_start_and_stop_are_idempotent(self) -> None:
        watcher = SudoInvocationWatcher(_record(), poll_interval=0.01)
        # Patch the polling target so we don't actually call psutil.
        with patch(
            "app.agents.sudo_invocations.psutil.Process",
            side_effect=psutil.NoSuchProcess(pid=os.getpid()),
        ):
            try:
                watcher.start()
                watcher.start()  # no-op
            finally:
                watcher.stop()
                watcher.stop()  # no-op

    def test_thread_actually_polls(self) -> None:
        # Drive a short-lived thread through one or two polls and assert
        # an event landed in the deque.
        watcher = SudoInvocationWatcher(_record(), poll_interval=0.01)
        fake_proc = _FakeProcess([_FakeChild(101, ["sudo", "ls"])])
        with patch("app.agents.sudo_invocations.psutil.Process", return_value=fake_proc):
            watcher.start()
            deadline = time.time() + 1.5
            while time.time() < deadline and not watcher.events():
                time.sleep(0.05)
            try:
                assert watcher.events(), "polling thread never recorded an event"
            finally:
                watcher.stop()


class TestCollectRecentSudoEvents:
    def test_lazy_start_aggregates_across_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bypass the real PID existence check so we don't need real PIDs.
        monkeypatch.setattr(sudo_module, "_agent_pid_exists", lambda _: True)
        # Replace SudoInvocationWatcher with a stub that doesn't spin a
        # thread; we only care about the coordinator's caching shape.
        started: list[str] = []

        class _StubWatcher:
            def __init__(self, record: AgentRecord, **_kwargs: Any) -> None:
                self._record = record

            def start(self) -> None:
                started.append(f"{self._record.name}:{self._record.pid}")

            def stop(self) -> None:
                pass

            def events_since(self, _since: float) -> list[SudoInvocationEvent]:
                return [
                    SudoInvocationEvent(
                        agent=f"{self._record.name}:{self._record.pid}",
                        command="sudo x",
                        child_pid=999,
                        timestamp=100.0,
                    )
                ]

        monkeypatch.setattr(sudo_module, "SudoInvocationWatcher", _StubWatcher)

        records = [_record(name="a1", pid=1), _record(name="a2", pid=2)]
        out = collect_recent_sudo_events(records, since=0.0)
        assert {e.agent for e in out} == {"a1:1", "a2:2"}
        assert sorted(started) == ["a1:1", "a2:2"]

    def test_unresolvable_pid_is_negative_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mirror the regression test from test_blast_radius.py: once a
        # PID is classified unresolvable, the resolver isn't re-invoked.
        calls: list[int] = []

        def fake_exists(record: AgentRecord) -> bool:
            calls.append(record.pid)
            return False

        monkeypatch.setattr(sudo_module, "_agent_pid_exists", fake_exists)
        records = [_record(pid=99999)]
        collect_recent_sudo_events(records, since=0.0)
        collect_recent_sudo_events(records, since=0.0)
        assert calls == [99999]
        assert "claude-code:99999" in sudo_module._UNRESOLVABLE

    def test_second_call_reuses_cached_watcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sudo_module, "_agent_pid_exists", lambda _: True)
        constructed: list[int] = []

        class _StubWatcher:
            def __init__(self, record: AgentRecord, **_kwargs: Any) -> None:
                constructed.append(record.pid)

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def events_since(self, _since: float) -> list[SudoInvocationEvent]:
                return []

        monkeypatch.setattr(sudo_module, "SudoInvocationWatcher", _StubWatcher)
        records = [_record(pid=1)]
        collect_recent_sudo_events(records, since=0.0)
        collect_recent_sudo_events(records, since=0.0)
        assert constructed == [1]


class TestEventsLockSerialization:
    """Light smoke check that the lock is held around append vs. read.

    Not a true race-detection test (would need many millions of
    iterations to stress), but a sanity that ``events()`` and a
    concurrent ``_record_event`` simulation co-exist without raising.
    """

    def test_concurrent_append_and_iter_does_not_raise(self) -> None:
        watcher = SudoInvocationWatcher(_record(), poll_interval=10.0)
        stop = threading.Event()

        def appender() -> None:
            i = 0
            while not stop.is_set():
                with watcher._events_lock:
                    watcher._events.append(
                        SudoInvocationEvent(
                            agent="a:1",
                            command=f"sudo {i}",
                            child_pid=i,
                            timestamp=float(i),
                        )
                    )
                i += 1

        t = threading.Thread(target=appender, daemon=True)
        t.start()
        try:
            for _ in range(200):
                # ``events()`` builds a list under the lock; should not
                # race against the appender.
                watcher.events()
        finally:
            stop.set()
            t.join(timeout=1.0)
