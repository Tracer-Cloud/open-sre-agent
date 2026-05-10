"""Filesystem blast-radius watcher for the monitor-local-agents fleet view.

Watches each registered agent's project root via ``watchdog`` and emits
:class:`BlastRadiusEvent` records when writes are observed. Events project
to :class:`app.agents.conflicts.WriteEvent` so the same source feeds both
the future ``/agents inspect <pid>`` blast-radius panel and the existing
``/agents conflicts`` cross-agent collision detector.

Scope of THIS module: file-write observation via ``watchdog``. Out of scope
in the parent issue's first phase: sudo invocation detection (#1500b) and
network-egress detection (#1500c). Those ship as separate watchers in
follow-up issues so this module's surface stays tight.

Cross-platform: macOS and Linux per issue #1500's acceptance criteria.
``watchdog`` chooses the native backend (FSEvents on macOS, inotify on
Linux) at runtime. Windows is intentionally not supported.

Lifecycle: the conflict detector calls :func:`collect_recent_write_events`
each time a user runs ``/agents conflicts``. The first call per
``(name, pid)`` resolves the agent's project root (via the agent process's
``cwd`` walked up to the nearest ``.git``) and starts a
``watchdog.observers.Observer`` thread; subsequent calls reuse the running
observer. ``atexit`` stops every watcher on interpreter exit. A future PR
(#1490 background task) can replace this lazy pattern with explicit
session-scoped ownership; the public surface stays unchanged.

The acceptance criterion for the parent probe issue (#1489) requires that
``psutil`` stay confined to an explicit allowlist; this module's use of
``psutil.Process(pid).cwd()`` is the second allowed consumer. See
``tests/agents/test_probe.py`` for the static containment scan.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import psutil
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from app.agents.conflicts import WriteEvent
from app.agents.registry import AgentRecord

logger = logging.getLogger(__name__)


# Per-watcher upper bound on retained events. Individual deque operations
# (``append``, ``popleft``) are GIL-atomic, but ``list(deque)`` iteration
# is NOT — a concurrent ``append`` on the watchdog callback thread races
# with iteration on the main thread and raises
# ``RuntimeError("deque mutated during iteration")``. A ``threading.Lock``
# shared with ``_BlastRadiusEventHandler`` serializes writes against reads.
# 10K events at typical observed rates (~10/s during a heavy build) gives
# ~16 minutes of history before eviction — comfortably more than the
# conflict detector's ``window_seconds`` (10s default).
_DEFAULT_MAX_EVENTS = 10_000


@dataclass(frozen=True)
class BlastRadiusEvent:
    """A single observed file write attributed to a monitored agent.

    ``agent`` is ``"{name}:{pid}"`` to match
    :attr:`app.agents.conflicts.WriteEvent.agent` so the same record can
    feed both the blast-radius panel and the cross-agent conflict detector
    without translation.

    ``outside_project_root`` is ``True`` when the write target is not
    under the agent's project root at the time the event fires. The flag
    is the value the future ``/agents inspect`` panel renders; in-tree
    writes are still recorded so the same stream feeds the existing
    cross-agent conflict detector without a second watcher.
    """

    agent: str
    path: str
    timestamp: float
    outside_project_root: bool


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upward from ``start`` (or cwd) looking for a ``.git`` directory.

    Returns the resolved repo root, or ``None`` if no ``.git`` is found
    walking up to the filesystem root. Symlinks in the search path are
    resolved before the walk; the walk itself follows ``.parent`` and so
    does not re-resolve at every step.

    The OpenSRE repo currently has no project-root resolver in
    ``app/utils/`` (verified at the time #1500 shipped); this helper is
    the first one. Future callers should reuse it rather than
    re-implementing.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


class _BlastRadiusEventHandler(FileSystemEventHandler):
    """Internal watchdog handler — translates filesystem events to
    :class:`BlastRadiusEvent` records.

    Not part of the public surface; tests should drive
    :class:`BlastRadiusWatcher` end-to-end or call :meth:`_record`
    directly with synthesized :class:`watchdog.events.FileSystemEvent`
    instances.
    """

    def __init__(
        self,
        agent_id: str,
        project_root: Path,
        sink: deque[BlastRadiusEvent],
        sink_lock: threading.Lock,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._project_root = project_root
        self._sink = sink
        self._sink_lock = sink_lock

    def on_modified(self, event: FileSystemEvent) -> None:
        self._record(event, event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        self._record(event, event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # ``on_moved`` reports both src and dest; the destination is the
        # write target, so attribute the event to ``dest_path`` and fall
        # back to ``src_path`` if the platform backend omits dest (rare).
        dest = getattr(event, "dest_path", "") or event.src_path
        self._record(event, dest)

    def _record(self, event: FileSystemEvent, raw_path: str | bytes) -> None:
        if event.is_directory:
            return
        # ``os.fsdecode`` is the canonical str-or-bytes path handler:
        # accepts both, decodes bytes per the filesystem encoding with
        # surrogateescape, never raises on malformed input.
        try:
            resolved = Path(os.fsdecode(raw_path)).resolve()
        except OSError:
            # Path that disappeared between the kernel notify and our
            # ``resolve()`` — can happen during rapid rename/delete bursts.
            # Drop the event rather than emit a half-formed record.
            return
        try:
            outside = not resolved.is_relative_to(self._project_root)
        except ValueError:
            # Different drive on Windows; we don't claim Windows support
            # but this branch keeps the function total under unexpected
            # platform layouts.
            outside = True
        evt = BlastRadiusEvent(
            agent=self._agent_id,
            path=str(resolved),
            timestamp=time.time(),
            outside_project_root=outside,
        )
        # Mutating the deque from the watchdog callback thread races
        # with iteration on the main thread (``list(deque)`` raises
        # ``RuntimeError("deque mutated during iteration")``). The lock
        # is shared with :class:`BlastRadiusWatcher` so the callback
        # serializes against ``events()`` /
        # ``write_events_for_conflicts()``.
        with self._sink_lock:
            self._sink.append(evt)


class BlastRadiusWatcher:
    """Watches one agent's project root and accumulates ``BlastRadiusEvent`` records.

    The watcher uses ``watchdog.observers.Observer`` with a recursive
    schedule on ``project_root``. Events are appended to a bounded
    :class:`collections.deque` (``maxlen=max_events``) — when the deque
    is full, the oldest event is dropped automatically. A
    :class:`threading.Lock` serializes the watchdog callback's append
    against the main-thread reader's iteration; without it,
    ``list(deque)`` would race against a concurrent ``append`` and raise
    ``RuntimeError("deque mutated during iteration")``.

    ``start()`` and ``stop()`` are idempotent. ``stop()`` joins the
    observer with a 5-second timeout; if the join times out a warning is
    logged but no exception escapes — REPL exit must not block on a hung
    filesystem-notification thread.
    """

    def __init__(
        self,
        record: AgentRecord,
        project_root: Path,
        *,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        self._record = record
        self._project_root = project_root
        self._events: deque[BlastRadiusEvent] = deque(maxlen=max_events)
        self._events_lock = threading.Lock()
        self._observer: BaseObserver | None = None

    @property
    def agent_id(self) -> str:
        return f"{self._record.name}:{self._record.pid}"

    @property
    def project_root(self) -> Path:
        return self._project_root

    def start(self) -> None:
        """Begin watching. Idempotent."""
        if self._observer is not None:
            return
        observer = Observer()
        observer.schedule(
            _BlastRadiusEventHandler(
                agent_id=self.agent_id,
                project_root=self._project_root,
                sink=self._events,
                sink_lock=self._events_lock,
            ),
            str(self._project_root),
            recursive=True,
        )
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        """Stop watching. Idempotent."""
        if self._observer is None:
            return
        observer = self._observer
        self._observer = None
        observer.stop()
        observer.join(timeout=5.0)
        if observer.is_alive():
            logger.warning(
                "blast-radius observer for %s did not stop within 5s; abandoning thread",
                self.agent_id,
            )

    def events(self) -> list[BlastRadiusEvent]:
        """Return a snapshot of observed events, oldest-first.

        The snapshot is a fresh ``list`` — mutating it does not affect
        the watcher's internal deque.
        """
        with self._events_lock:
            return list(self._events)

    def write_events_for_conflicts(self, since: float) -> list[WriteEvent]:
        """Project events to :class:`WriteEvent` shape, filtered by wall-clock cutoff.

        ``since`` is a unix-seconds threshold; events older than ``since``
        are excluded. This is the pre-filter the
        :func:`app.agents.conflicts.detect_conflicts` contract requires —
        without it, anchor-based windowing would slide backward when no
        new writes arrive and stale conflicts would never roll off.
        """
        with self._events_lock:
            return [
                WriteEvent(agent=e.agent, path=e.path, timestamp=e.timestamp)
                for e in self._events
                if e.timestamp >= since
            ]


# Process-global watcher cache. Keyed by ``f"{name}:{pid}"`` so two
# differently-named entries for the same PID don't collide. Mutated only
# under ``_WATCHERS_LOCK`` because Python dict insertion isn't atomic
# across two threads racing to start the same watcher.
_WATCHERS: dict[str, BlastRadiusWatcher] = {}
# Negative cache of agent keys whose project root could not be resolved
# (dead/zombie PID, access denied, or no ``.git`` ancestor). Without this
# set, every ``/agents conflicts`` invocation would re-enter ``psutil`` for
# the same stale registry entry; with it, the second call short-circuits
# to ``None`` without touching ``psutil``. Guarded by ``_WATCHERS_LOCK``.
_UNRESOLVABLE: set[str] = set()
_WATCHERS_LOCK = threading.Lock()


def _resolve_agent_project_root(record: AgentRecord) -> Path | None:
    """Resolve ``record``'s project root by reading its process ``cwd``, then walking up.

    Returns ``None`` for PIDs we can't introspect — missing PID, zombie,
    cross-user on macOS, or any case where ``cwd()`` raises. Mirrors the
    EAFP failure modes documented in :func:`app.agents.probe.probe`. Each
    failure is logged at DEBUG so a developer can grep without polluting
    REPL stdout.
    """
    try:
        proc = psutil.Process(record.pid)
        agent_cwd = Path(proc.cwd())
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, ProcessLookupError):
        logger.debug("blast-radius: cannot resolve cwd for pid=%s", record.pid)
        return None
    return find_project_root(agent_cwd)


def _get_or_start_watcher(record: AgentRecord) -> BlastRadiusWatcher | None:
    """Return the running watcher for ``record``, starting it on first call.

    Returns ``None`` if a project root can't be resolved (PID gone, no
    ``.git`` ancestor, or access denied). The unresolvable key is recorded
    in ``_UNRESOLVABLE`` so subsequent calls for the same stale registry
    entry short-circuit without re-invoking ``psutil`` — important for
    long-running REPL sessions where ``/agents conflicts`` may run
    repeatedly against entries whose PIDs never come back. Cleanup is
    registered via ``atexit`` outside the lock so atexit's internal lock
    acquisition can never contend with future watcher operations.
    """
    key = f"{record.name}:{record.pid}"
    with _WATCHERS_LOCK:
        cached = _WATCHERS.get(key)
        if cached is not None:
            return cached
        if key in _UNRESOLVABLE:
            return None
        project_root = _resolve_agent_project_root(record)
        if project_root is None:
            _UNRESOLVABLE.add(key)
            return None
        watcher = BlastRadiusWatcher(record, project_root)
        watcher.start()
        _WATCHERS[key] = watcher
    atexit.register(watcher.stop)
    return watcher


def collect_recent_write_events(
    records: Iterable[AgentRecord],
    *,
    since: float,
) -> list[WriteEvent]:
    """Lazy-start watchers for each ``records`` entry and aggregate ``WriteEvent`` records.

    First call per ``(name, pid)`` resolves the agent's project root,
    instantiates a :class:`BlastRadiusWatcher`, starts it, and registers
    an ``atexit`` callback to stop it on interpreter exit. Subsequent
    calls reuse the cached watcher. Records whose PID can't be
    introspected are silently skipped per the probe.py EAFP contract.

    ``since`` is forwarded to
    :meth:`BlastRadiusWatcher.write_events_for_conflicts` on each
    watcher so the conflict detector receives only events newer than
    that wall-clock cutoff.
    """
    events: list[WriteEvent] = []
    for record in records:
        watcher = _get_or_start_watcher(record)
        if watcher is None:
            continue
        events.extend(watcher.write_events_for_conflicts(since))
    return events


def _reset_watchers_for_tests() -> None:
    """Stop all running watchers and clear the cache. Test-only helper.

    Call from a pytest fixture or teardown to reset module-global state
    between tests. Production code should never call this.
    """
    with _WATCHERS_LOCK:
        watchers = list(_WATCHERS.values())
        _WATCHERS.clear()
        _UNRESOLVABLE.clear()
    for w in watchers:
        w.stop()


__all__ = [
    "BlastRadiusEvent",
    "BlastRadiusWatcher",
    "collect_recent_write_events",
    "find_project_root",
]
