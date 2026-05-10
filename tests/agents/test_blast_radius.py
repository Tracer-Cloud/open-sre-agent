"""Tests for ``app/agents/blast_radius.py`` (issue #1500).

Covers four layers of the blast-radius watcher:

1. ``find_project_root`` — pure ``.git`` walk
2. ``_BlastRadiusEventHandler`` — translation of watchdog events to
   :class:`BlastRadiusEvent` records, driven by synthesized
   :class:`watchdog.events.FileSystemEvent` objects (no real filesystem)
3. :class:`BlastRadiusWatcher` — observer lifecycle and event projection
   to :class:`WriteEvent` shape
4. :func:`collect_recent_write_events` — lazy coordinator with the
   process-global watcher cache

Plus one slow end-to-end smoke test using a real watchdog observer on a
``tmp_path`` directory; gated behind the ``slow`` marker so the default
``make test-cov`` run stays deterministic.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import pytest
from watchdog.events import (
    DirModifiedEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from app.agents import blast_radius as blast_radius_module
from app.agents.blast_radius import (
    BlastRadiusEvent,
    BlastRadiusWatcher,
    _BlastRadiusEventHandler,
    _reset_watchers_for_tests,
    collect_recent_write_events,
    find_project_root,
)
from app.agents.conflicts import WriteEvent
from app.agents.registry import AgentRecord


@pytest.fixture(autouse=True)
def _reset_watcher_cache() -> Iterator[None]:
    """Reset the module-global watcher cache before and after each test.

    Without this fixture, a watcher started by an earlier test would
    survive across tests via ``_WATCHERS`` and any later
    ``collect_recent_write_events`` call would hit the cached watcher
    instead of exercising the lazy-start path under test.
    """
    _reset_watchers_for_tests()
    yield
    _reset_watchers_for_tests()


def _record(name: str = "claude-code", pid: int = 8421) -> AgentRecord:
    return AgentRecord(name=name, pid=pid, command="claude")


class TestFindProjectRoot:
    def test_returns_git_dir_when_found_via_walk_up(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert find_project_root(nested) == tmp_path.resolve()

    def test_returns_root_itself_when_start_already_has_git(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert find_project_root(tmp_path) == tmp_path.resolve()

    def test_returns_none_when_no_git_found_walking_to_root(self, tmp_path: Path) -> None:
        # tmp_path lives under /private/var/folders (macOS) or /tmp (linux);
        # neither is inside a git checkout, so walking to / never finds .git.
        assert find_project_root(tmp_path) is None

    def test_resolves_symlinks_in_start_path(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "real" / "deep").mkdir(parents=True)
        link = tmp_path / "link"
        link.symlink_to(tmp_path / "real")
        assert find_project_root(link / "deep") == tmp_path.resolve()


class TestBlastRadiusEventDataclass:
    def test_is_frozen_and_hashable(self) -> None:
        e = BlastRadiusEvent(
            agent="claude-code:1", path="/x", timestamp=10.0, outside_project_root=True
        )
        # Frozen → ``hash`` works; assigning a field raises.
        assert e in {e}
        with pytest.raises(AttributeError):
            e.path = "/y"  # type: ignore[misc]

    def test_outside_project_root_is_part_of_identity(self) -> None:
        in_tree = BlastRadiusEvent(
            agent="a:1", path="/x", timestamp=1.0, outside_project_root=False
        )
        out_tree = BlastRadiusEvent(
            agent="a:1", path="/x", timestamp=1.0, outside_project_root=True
        )
        assert in_tree != out_tree


class TestBlastRadiusEventHandler:
    """Drives ``_BlastRadiusEventHandler`` with synthesized watchdog events.

    These tests don't touch the filesystem watcher at all — they call
    ``on_modified`` / ``on_created`` / ``on_moved`` directly with
    constructed :class:`watchdog.events.FileSystemEvent` instances and
    assert on the resulting :class:`BlastRadiusEvent` records dropped
    into the deque sink.
    """

    def _build(
        self, project_root: Path
    ) -> tuple[_BlastRadiusEventHandler, deque[BlastRadiusEvent]]:
        sink: deque[BlastRadiusEvent] = deque(maxlen=10)
        handler = _BlastRadiusEventHandler("claude-code:1", project_root, sink, threading.Lock())
        return handler, sink

    def test_on_modified_emits_event_for_in_tree_file(self, tmp_path: Path) -> None:
        f = tmp_path / "in_tree.py"
        f.write_text("x", encoding="utf-8")
        handler, sink = self._build(tmp_path)
        handler.on_modified(FileModifiedEvent(str(f)))
        assert len(sink) == 1
        evt = sink[0]
        assert evt.agent == "claude-code:1"
        assert evt.path == str(f.resolve())
        assert evt.outside_project_root is False

    def test_on_created_emits_event(self, tmp_path: Path) -> None:
        f = tmp_path / "new.py"
        f.write_text("x", encoding="utf-8")
        handler, sink = self._build(tmp_path)
        handler.on_created(FileCreatedEvent(str(f)))
        assert len(sink) == 1

    def test_on_moved_attributes_event_to_dest_path(self, tmp_path: Path) -> None:
        src = tmp_path / "src.py"
        dest = tmp_path / "dest.py"
        src.write_text("x", encoding="utf-8")
        dest.write_text("y", encoding="utf-8")
        handler, sink = self._build(tmp_path)
        handler.on_moved(FileMovedEvent(str(src), str(dest)))
        assert len(sink) == 1
        assert sink[0].path == str(dest.resolve())

    def test_directory_events_are_ignored(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        handler, sink = self._build(tmp_path)
        handler.on_modified(DirModifiedEvent(str(sub)))
        assert len(sink) == 0

    def test_out_of_tree_write_flagged(self, tmp_path: Path) -> None:
        # Project root is a subdir of tmp_path; write happens above it.
        project_root = tmp_path / "repo"
        project_root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("danger", encoding="utf-8")
        handler, sink = self._build(project_root)
        handler.on_modified(FileModifiedEvent(str(outside)))
        assert len(sink) == 1
        assert sink[0].outside_project_root is True

    def test_sink_is_bounded_by_maxlen(self, tmp_path: Path) -> None:
        # Build a handler with a 3-event sink; emit 5 events; oldest two drop.
        sink: deque[BlastRadiusEvent] = deque(maxlen=3)
        handler = _BlastRadiusEventHandler("a:1", tmp_path, sink, threading.Lock())
        for i in range(5):
            f = tmp_path / f"f{i}.py"
            f.write_text("x", encoding="utf-8")
            handler.on_modified(FileModifiedEvent(str(f)))
        assert len(sink) == 3
        # The retained events should be the three most-recent paths.
        retained_paths = {e.path for e in sink}
        assert str((tmp_path / "f4.py").resolve()) in retained_paths
        assert str((tmp_path / "f0.py").resolve()) not in retained_paths


class TestBlastRadiusWatcher:
    def test_start_and_stop_are_idempotent(self, tmp_path: Path) -> None:
        watcher = BlastRadiusWatcher(_record(), tmp_path)
        try:
            watcher.start()
            watcher.start()  # second call must be a no-op
        finally:
            watcher.stop()
            watcher.stop()  # second call must be a no-op

    def test_events_returns_snapshot_copy(self, tmp_path: Path) -> None:
        watcher = BlastRadiusWatcher(_record(), tmp_path)
        watcher._events.append(  # private — testing the snapshot semantic
            BlastRadiusEvent(agent="a:1", path="/x", timestamp=1.0, outside_project_root=False)
        )
        snapshot = watcher.events()
        snapshot.clear()
        # The internal deque must be unaffected by mutations to the snapshot.
        assert len(watcher.events()) == 1

    def test_write_events_for_conflicts_filters_by_since(self, tmp_path: Path) -> None:
        watcher = BlastRadiusWatcher(_record(), tmp_path)
        for ts in (10.0, 20.0, 30.0):
            watcher._events.append(
                BlastRadiusEvent(
                    agent="a:1", path=f"/x{ts}", timestamp=ts, outside_project_root=False
                )
            )
        out = watcher.write_events_for_conflicts(since=20.0)
        assert [e.timestamp for e in out] == [20.0, 30.0]
        assert all(isinstance(e, WriteEvent) for e in out)

    def test_write_events_for_conflicts_drops_outside_root_flag(self, tmp_path: Path) -> None:
        # ``WriteEvent`` does not carry ``outside_project_root``; the
        # projection must drop it. Both in-tree and out-of-tree blast
        # events flow into the conflict detector identically.
        watcher = BlastRadiusWatcher(_record(), tmp_path)
        watcher._events.append(
            BlastRadiusEvent(
                agent="a:1", path="/inside", timestamp=10.0, outside_project_root=False
            )
        )
        watcher._events.append(
            BlastRadiusEvent(
                agent="a:1", path="/outside", timestamp=11.0, outside_project_root=True
            )
        )
        out = watcher.write_events_for_conflicts(since=0.0)
        paths = {e.path for e in out}
        assert paths == {"/inside", "/outside"}

    def test_agent_id_is_name_colon_pid(self) -> None:
        watcher = BlastRadiusWatcher(_record(name="cursor", pid=9001), Path("/"))
        assert watcher.agent_id == "cursor:9001"


class TestCollectRecentWriteEvents:
    def test_lazy_start_aggregates_across_agents(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Stub the project-root resolver so we don't need real PIDs.
        roots = {1: tmp_path / "agent1", 2: tmp_path / "agent2"}
        for r in roots.values():
            r.mkdir()
        monkeypatch.setattr(
            blast_radius_module,
            "_resolve_agent_project_root",
            lambda record: roots.get(record.pid),
        )

        records = [_record(name="a1", pid=1), _record(name="a2", pid=2)]

        # Empty result on first call (no events yet) but watchers are now started.
        out = collect_recent_write_events(records, since=0.0)
        assert out == []
        assert len(blast_radius_module._WATCHERS) == 2

        # Inject events into both watchers; second call should aggregate.
        for key, root in (("a1:1", roots[1]), ("a2:2", roots[2])):
            watcher = blast_radius_module._WATCHERS[key]
            watcher._events.append(
                BlastRadiusEvent(
                    agent=key, path=str(root / "x"), timestamp=100.0, outside_project_root=False
                )
            )

        out = collect_recent_write_events(records, since=0.0)
        assert len(out) == 2
        assert {e.agent for e in out} == {"a1:1", "a2:2"}

    def test_second_call_reuses_cached_watcher(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        calls: list[int] = []

        def fake_resolver(record: AgentRecord) -> Path:
            calls.append(record.pid)
            return tmp_path

        monkeypatch.setattr(blast_radius_module, "_resolve_agent_project_root", fake_resolver)

        records = [_record(pid=1)]
        collect_recent_write_events(records, since=0.0)
        collect_recent_write_events(records, since=0.0)
        # Resolver only invoked on the first call; second hits the cache.
        assert calls == [1]

    def test_records_with_unresolvable_pid_are_silently_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(blast_radius_module, "_resolve_agent_project_root", lambda _: None)
        records = [_record(pid=99999)]
        out = collect_recent_write_events(records, since=0.0)
        assert out == []
        assert blast_radius_module._WATCHERS == {}

    def test_unresolvable_pid_is_negative_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Second call must not re-enter the resolver (which on real
        # systems calls ``psutil.Process(pid).cwd()`` — the bug Greptile
        # flagged). Once a PID has been classified unresolvable, the key
        # short-circuits to ``None``.
        calls: list[int] = []

        def failing_resolver(record: AgentRecord) -> None:
            calls.append(record.pid)
            return None

        monkeypatch.setattr(blast_radius_module, "_resolve_agent_project_root", failing_resolver)
        records = [_record(pid=99999)]
        collect_recent_write_events(records, since=0.0)
        collect_recent_write_events(records, since=0.0)
        assert calls == [99999]
        assert "claude-code:99999" in blast_radius_module._UNRESOLVABLE

    def test_since_is_forwarded_per_watcher(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(blast_radius_module, "_resolve_agent_project_root", lambda _: tmp_path)
        records = [_record(pid=1)]
        # Bootstrap watcher.
        collect_recent_write_events(records, since=0.0)
        watcher = blast_radius_module._WATCHERS["claude-code:1"]
        # Two events: one before cutoff, one after.
        watcher._events.append(
            BlastRadiusEvent(
                agent="claude-code:1", path="/old", timestamp=10.0, outside_project_root=False
            )
        )
        watcher._events.append(
            BlastRadiusEvent(
                agent="claude-code:1", path="/new", timestamp=30.0, outside_project_root=False
            )
        )
        out = collect_recent_write_events(records, since=20.0)
        assert [e.path for e in out] == ["/new"]


@pytest.mark.slow
class TestE2ESmoke:
    def test_real_watchdog_captures_a_real_write(self, tmp_path: Path) -> None:
        """Sanity check that the wired-up watcher actually receives events.

        Marked ``slow`` because watchdog's macOS FSEvents backend has a
        ~1-2s coalescing delay that flake-magnifies on shared CI runners.
        Default ``make test-cov`` skips this; CI's slow lane runs it.
        """
        watcher = BlastRadiusWatcher(_record(), tmp_path)
        watcher.start()
        try:
            (tmp_path / "smoke.txt").write_text("hello", encoding="utf-8")
            deadline = time.time() + 3.0
            while time.time() < deadline and not watcher.events():
                time.sleep(0.1)
            assert watcher.events(), "watchdog observer never reported the write"
        finally:
            watcher.stop()
