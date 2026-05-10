"""Sudo invocation watcher for the monitor-local-agents fleet view.

Detects ``sudo`` (and BSD-derived ``doas``) invocations spawned by a
monitored agent by polling :meth:`psutil.Process.children` recursively
and matching the head of each child's ``cmdline``. Events feed the
``/agents inspect <pid>`` blast-radius panel — the second of three
watcher streams alongside :mod:`app.agents.blast_radius` and
:mod:`app.agents.network_egress`.

Why polling and not eBPF / audit subsystem?  ``auditd`` is Linux-only
and requires root; eBPF needs kernel headers that don't exist on
macOS. The ~1 s poll interval is a pragmatic cross-platform compromise:
short enough to catch typical sudo invocations (which usually live for
seconds while the user types a password or runs a command), long
enough to keep CPU overhead negligible. Sub-second invocations may be
missed; the panel renders with an "approximate" caveat in the header.

The acceptance criterion for the parent probe issue (#1489) requires
that ``psutil`` stay confined to an explicit allowlist; this module is
the third allowed consumer alongside ``probe.py`` and
``blast_radius.py``. See ``tests/agents/test_probe.py`` for the static
containment scan.

Lifecycle: same lazy-start pattern as :mod:`app.agents.blast_radius` —
the inspect panel calls :func:`collect_recent_sudo_events` per render;
the first call per ``(name, pid)`` starts a polling thread, subsequent
calls reuse it. ``atexit`` stops every watcher on interpreter exit.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

import psutil

from app.agents.registry import AgentRecord

logger = logging.getLogger(__name__)


# Per-watcher upper bound on retained events. The list is drained by
# the inspect panel render, never by an external consumer; 10K covers
# any realistic agent session before eviction. Bounded ``deque`` is
# guarded by ``threading.Lock`` (``list(deque)`` iteration is NOT
# GIL-atomic against a concurrent ``append``).
_DEFAULT_MAX_EVENTS = 10_000

# Polling cadence for ``Process.children(recursive=True)``. 1 second is
# the same trade-off pthreads-based monitors (htop, glances) settle on:
# short enough to catch interactive sudo, long enough to keep CPU
# overhead under 0.1 % on idle agents.
_DEFAULT_POLL_INTERVAL_S = 1.0

# Process-name heads that count as sudo invocations. ``sudo`` covers
# every Linux distribution and macOS; ``doas`` is the OpenBSD-derived
# alternative shipped on some Alpine and FreeBSD-based systems. We
# match the head only — ``sudo -u app foo`` and ``sudoedit`` both fire,
# but ``pseudo`` does not.
_SUDO_BINARIES = frozenset({"sudo", "doas", "sudoedit"})


@dataclass(frozen=True)
class SudoInvocationEvent:
    """A single observed sudo (or doas) invocation by a monitored agent.

    ``agent`` is ``"{name}:{pid}"`` to match the same agent-id shape
    used elsewhere (see :class:`app.agents.blast_radius.BlastRadiusEvent`).

    ``command`` is the full ``cmdline`` of the sudo child as a single
    space-joined string — what the user would have typed. ``child_pid``
    is the PID of the sudo subprocess (not the agent), so the inspect
    panel can show ``sudo apt update (pid 12345)`` and a future
    follow-up can drill into per-invocation outcome.
    """

    agent: str
    command: str
    child_pid: int
    timestamp: float


class SudoInvocationWatcher:
    """Polls ``psutil.Process(pid).children(recursive=True)`` for sudo invocations.

    Runs a daemon thread that wakes every ``poll_interval`` seconds.
    Each tick walks the recursive child set, checks the head of each
    cmdline against :data:`_SUDO_BINARIES`, and appends a
    :class:`SudoInvocationEvent` for any *new* sudo PID it hasn't seen
    before. The dedup set is per-watcher; restarting the watcher
    forgets prior PIDs (acceptable — the monotone PID space makes
    accidental dedup-against-stale-PID essentially impossible within
    a single OS uptime cycle).

    ``start()`` and ``stop()`` are idempotent. ``stop()`` joins the
    thread with a 5-second timeout; if it doesn't exit a warning is
    logged but no exception escapes — REPL exit must not block on a
    hung psutil call.
    """

    def __init__(
        self,
        record: AgentRecord,
        *,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_S,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        self._record = record
        self._poll_interval = poll_interval
        self._events: deque[SudoInvocationEvent] = deque(maxlen=max_events)
        self._events_lock = threading.Lock()
        self._seen_pids: set[int] = set()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def agent_id(self) -> str:
        return f"{self._record.name}:{self._record.pid}"

    def start(self) -> None:
        """Begin polling. Idempotent."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"sudo-watch:{self.agent_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop polling. Idempotent."""
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        self._stop_event.set()
        thread.join(timeout=5.0)
        if thread.is_alive():
            logger.warning(
                "sudo watcher for %s did not stop within 5s; abandoning thread",
                self.agent_id,
            )

    def events(self) -> list[SudoInvocationEvent]:
        """Return a snapshot of observed sudo invocations, oldest-first."""
        with self._events_lock:
            return list(self._events)

    def events_since(self, since: float) -> list[SudoInvocationEvent]:
        """Return events with ``timestamp >= since`` (unix-seconds)."""
        with self._events_lock:
            return [e for e in self._events if e.timestamp >= since]

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            # ``Event.wait`` returns True if set during the wait, which
            # short-circuits the next iteration's ``is_set`` check.
            if self._stop_event.wait(self._poll_interval):
                return

    def _poll_once(self) -> None:
        try:
            proc = psutil.Process(self._record.pid)
            children = proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            # Agent has exited or moved out of reach. The watcher keeps
            # ticking so a re-spawned process under the same PID would
            # be picked up; exiting silently here would require a
            # registry-level signal we don't have.
            return

        for child in children:
            try:
                if child.pid in self._seen_pids:
                    continue
                cmdline = child.cmdline()
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                # Child raced — exited between ``children()`` and
                # ``cmdline()``. Skip; if it really was sudo we missed
                # it, but the next poll will catch a successor.
                continue
            if not cmdline:
                continue
            head = cmdline[0].rsplit("/", 1)[-1]
            if head not in _SUDO_BINARIES:
                continue
            evt = SudoInvocationEvent(
                agent=self.agent_id,
                command=" ".join(cmdline),
                child_pid=child.pid,
                timestamp=time.time(),
            )
            with self._events_lock:
                # Co-bound the dedup set with the deque: when the deque
                # is at capacity the next ``append`` will evict the
                # oldest event, so drop its child PID from ``_seen_pids``
                # to keep the set in lockstep. Without this, the set
                # would grow without bound for the lifetime of the REPL
                # session even though the deque caps at ``maxlen``. A
                # PID that re-appears after eviction (extremely rare in
                # practice — would require both PID reuse and a fresh
                # sudo invocation under the same PID) emits a fresh
                # event, which is the correct behavior.
                if len(self._events) == self._events.maxlen:
                    oldest = self._events[0]
                    self._seen_pids.discard(oldest.child_pid)
                self._events.append(evt)
                self._seen_pids.add(child.pid)


# Process-global watcher cache. Keyed by ``f"{name}:{pid}"`` so two
# differently-named entries for the same PID don't collide. Mutated only
# under ``_WATCHERS_LOCK``.
_WATCHERS: dict[str, SudoInvocationWatcher] = {}
# Negative cache of agent keys whose watcher couldn't be started
# (currently only ``ProcessLookupError`` from ``psutil.Process(pid)`` —
# every other failure falls through to a watcher that polls forever
# returning empty events). Mirrors :mod:`app.agents.blast_radius`'s
# negative cache so repeated ``/agents inspect`` calls don't retry
# ``psutil`` on stale registry entries.
_UNRESOLVABLE: set[str] = set()
_WATCHERS_LOCK = threading.Lock()


def _agent_pid_exists(record: AgentRecord) -> bool:
    """Return whether ``record.pid`` corresponds to a process we can introspect."""
    try:
        # ``psutil.Process`` raises ``NoSuchProcess`` for PIDs the OS
        # doesn't know about; ``AccessDenied`` is a "yes it exists, but
        # we can't read it" case which we still treat as existing
        # because the watcher will silently skip events under that
        # branch on each poll.
        psutil.Process(record.pid)
    except (psutil.NoSuchProcess, ProcessLookupError):
        return False
    return True


def _get_or_start_watcher(record: AgentRecord) -> SudoInvocationWatcher | None:
    """Return the running sudo watcher for ``record``, starting it on first call.

    Returns ``None`` if the agent's PID can't be resolved at all
    (process gone). Subsequent calls for the same key short-circuit via
    ``_UNRESOLVABLE``. ``atexit`` cleanup is registered outside the
    lock so atexit's internal lock acquisition can never contend with
    future watcher operations.
    """
    key = f"{record.name}:{record.pid}"
    with _WATCHERS_LOCK:
        cached = _WATCHERS.get(key)
        if cached is not None:
            return cached
        if key in _UNRESOLVABLE:
            return None
        if not _agent_pid_exists(record):
            _UNRESOLVABLE.add(key)
            return None
        watcher = SudoInvocationWatcher(record)
        watcher.start()
        _WATCHERS[key] = watcher
    atexit.register(watcher.stop)
    return watcher


def collect_recent_sudo_events(
    records: Iterable[AgentRecord],
    *,
    since: float,
) -> list[SudoInvocationEvent]:
    """Lazy-start watchers per record and aggregate events newer than ``since``.

    Mirrors :func:`app.agents.blast_radius.collect_recent_write_events`
    so the inspect panel can call all three watcher coordinators with
    the same shape.
    """
    events: list[SudoInvocationEvent] = []
    for record in records:
        watcher = _get_or_start_watcher(record)
        if watcher is None:
            continue
        events.extend(watcher.events_since(since))
    return events


def _reset_watchers_for_tests() -> None:
    """Stop all running watchers and clear the cache. Test-only helper."""
    with _WATCHERS_LOCK:
        watchers = list(_WATCHERS.values())
        _WATCHERS.clear()
        _UNRESOLVABLE.clear()
    for w in watchers:
        w.stop()


__all__ = [
    "SudoInvocationEvent",
    "SudoInvocationWatcher",
    "collect_recent_sudo_events",
]
