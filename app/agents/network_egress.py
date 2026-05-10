"""Network-egress watcher for the monitor-local-agents fleet view.

Detects new outbound TCP/UDP connections opened by a monitored agent
by polling :meth:`psutil.Process.net_connections` and recording the
first sighting of each ``(remote_ip, remote_port)`` pair. Events feed
the ``/agents inspect <pid>`` blast-radius panel — the third of three
watcher streams alongside :mod:`app.agents.blast_radius` and
:mod:`app.agents.sudo_invocations`.

Dedup by ``(remote_ip, remote_port)`` rather than per-connection is a
deliberate trade-off: an agent making 1000 short-lived connections to
the same package mirror should produce one event ("first contact with
mirror.foo.org:443"), not 1000 spam rows. Listening sockets and
connections in ``LISTEN`` / ``NONE`` state are filtered out — the
panel cares about *new outbound destinations*, not bound local ports.

Why polling and not eBPF / pcap? eBPF requires root + recent kernel
on Linux and doesn't exist on macOS; pcap requires root and floods
events. ``psutil.net_connections`` is the cross-platform compromise
the rest of this module family already commits to. ~1 s cadence loses
sub-second connections; documented in the panel header.

The acceptance criterion for the parent probe issue (#1489) requires
that ``psutil`` stay confined to an explicit allowlist; this module
is the fourth allowed consumer alongside ``probe.py``,
``blast_radius.py``, and ``sudo_invocations.py``. See
``tests/agents/test_probe.py`` for the static containment scan.

Lifecycle: same lazy-start pattern as the other two watchers — the
inspect panel calls :func:`collect_recent_egress_events` per render;
the first call per ``(name, pid)`` starts a polling thread, subsequent
calls reuse it. ``atexit`` stops every watcher on interpreter exit.
"""

from __future__ import annotations

import atexit
import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

import psutil

from app.agents.registry import AgentRecord

logger = logging.getLogger(__name__)


_DEFAULT_MAX_EVENTS = 10_000
_DEFAULT_POLL_INTERVAL_S = 1.0

# Connection statuses that count as "outbound destination reached".
# ``ESTABLISHED`` is the obvious one; ``SYN_SENT`` covers connections
# in flight (and the timing of psutil's snapshot frequently catches
# fast hosts only in this state); ``CLOSE_WAIT`` and friends are still
# evidence of contact. We exclude ``LISTEN`` and ``NONE`` (UDP without
# a remote) and ``CLOSE`` (already torn down before our snapshot).
_OUTBOUND_STATES = frozenset(
    {
        psutil.CONN_ESTABLISHED,
        psutil.CONN_SYN_SENT,
        psutil.CONN_CLOSE_WAIT,
        psutil.CONN_FIN_WAIT1,
        psutil.CONN_FIN_WAIT2,
        psutil.CONN_LAST_ACK,
        psutil.CONN_TIME_WAIT,
    }
)


@dataclass(frozen=True)
class NetworkEgressEvent:
    """A single observed first-contact with an outbound host by an agent.

    ``agent`` is ``"{name}:{pid}"`` to match the same agent-id shape
    used elsewhere.

    ``remote_host`` and ``remote_port`` are the destination tuple. We
    don't try to reverse-DNS — DNS lookups in a polling loop add
    latency and produce flaky events when the resolver fails. The
    inspect panel renders the IP literally; users who want hostnames
    can look them up out-of-band.

    ``family`` is ``"ipv4"`` or ``"ipv6"`` so the panel can render
    addresses in their canonical form. Only ``socket.AF_INET`` and
    ``AF_INET6`` are tracked; ``AF_UNIX`` connections are local and
    don't represent egress.
    """

    agent: str
    remote_host: str
    remote_port: int
    family: str
    timestamp: float


class NetworkEgressWatcher:
    """Polls ``psutil.Process(pid).net_connections`` for new outbound hosts.

    Runs a daemon thread that wakes every ``poll_interval`` seconds.
    Each tick walks the connection list, filters by
    :data:`_OUTBOUND_STATES`, and emits a :class:`NetworkEgressEvent`
    only on first sighting of each ``(remote_host, remote_port,
    family)`` triple. The dedup set is per-watcher; restarting the
    watcher forgets prior hosts (acceptable — the panel's purpose is
    to show *what the agent did this session*, not perpetual history).

    ``start()`` and ``stop()`` are idempotent. ``stop()`` joins the
    thread with a 5-second timeout; if it doesn't exit a warning is
    logged but no exception escapes.
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
        self._events: deque[NetworkEgressEvent] = deque(maxlen=max_events)
        self._events_lock = threading.Lock()
        self._seen_destinations: set[tuple[str, int, str]] = set()
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
            name=f"netegress-watch:{self.agent_id}",
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
                "network-egress watcher for %s did not stop within 5s; abandoning thread",
                self.agent_id,
            )

    def events(self) -> list[NetworkEgressEvent]:
        """Return a snapshot of observed egress events, oldest-first."""
        with self._events_lock:
            return list(self._events)

    def events_since(self, since: float) -> list[NetworkEgressEvent]:
        """Return events with ``timestamp >= since`` (unix-seconds)."""
        with self._events_lock:
            return [e for e in self._events if e.timestamp >= since]

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            if self._stop_event.wait(self._poll_interval):
                return

    def _poll_once(self) -> None:
        try:
            proc = psutil.Process(self._record.pid)
            # ``net_connections`` is the new spelling; older psutil
            # versions only have ``connections``. The probe module
            # already handles this fallback; mirror it here so
            # forward-dated psutil drops of ``connections`` don't break
            # the watcher.
            getter = proc.net_connections if hasattr(proc, "net_connections") else proc.connections
            connections = getter(kind="inet")
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            return

        for conn in connections:
            if conn.status not in _OUTBOUND_STATES:
                continue
            raddr = conn.raddr
            if not raddr:
                # Some platforms report ``raddr`` as an empty tuple for
                # UDP sockets without a connect() call. Skip — no
                # destination to record.
                continue
            family = "ipv6" if conn.family == socket.AF_INET6 else "ipv4"
            remote_host = raddr.ip
            remote_port = int(raddr.port)
            key = (remote_host, remote_port, family)
            with self._events_lock:
                if key in self._seen_destinations:
                    # Skip BEFORE constructing the event — a busy agent
                    # may report the same already-known destination on
                    # every poll and allocating then discarding a
                    # dataclass for each is pure GC pressure.
                    continue
                # Co-bound the dedup set with the deque: when the deque
                # is at capacity the next ``append`` will evict the
                # oldest event, so drop its key from ``_seen_destinations``
                # to keep the set in lockstep. Without this, the set
                # would grow without bound for the lifetime of the REPL
                # session even though the deque caps at ``maxlen``. A
                # destination that re-appears after eviction will emit
                # a fresh event — that's the correct behavior because
                # the watcher has genuinely forgotten it.
                if len(self._events) == self._events.maxlen:
                    oldest = self._events[0]
                    self._seen_destinations.discard(
                        (oldest.remote_host, oldest.remote_port, oldest.family)
                    )
                self._events.append(
                    NetworkEgressEvent(
                        agent=self.agent_id,
                        remote_host=remote_host,
                        remote_port=remote_port,
                        family=family,
                        timestamp=time.time(),
                    )
                )
                self._seen_destinations.add(key)


# Process-global watcher cache.
_WATCHERS: dict[str, NetworkEgressWatcher] = {}
_UNRESOLVABLE: set[str] = set()
_WATCHERS_LOCK = threading.Lock()


def _agent_pid_exists(record: AgentRecord) -> bool:
    """Return whether ``record.pid`` corresponds to a process we can introspect."""
    try:
        psutil.Process(record.pid)
    except (psutil.NoSuchProcess, ProcessLookupError):
        return False
    return True


def _get_or_start_watcher(record: AgentRecord) -> NetworkEgressWatcher | None:
    """Return the running egress watcher for ``record``, starting it on first call."""
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
        watcher = NetworkEgressWatcher(record)
        watcher.start()
        _WATCHERS[key] = watcher
    atexit.register(watcher.stop)
    return watcher


def collect_recent_egress_events(
    records: Iterable[AgentRecord],
    *,
    since: float,
) -> list[NetworkEgressEvent]:
    """Lazy-start watchers per record and aggregate egress events newer than ``since``.

    Mirrors :func:`app.agents.blast_radius.collect_recent_write_events`
    and :func:`app.agents.sudo_invocations.collect_recent_sudo_events`
    so the inspect panel can call all three watcher coordinators with
    the same shape.
    """
    events: list[NetworkEgressEvent] = []
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
    "NetworkEgressEvent",
    "NetworkEgressWatcher",
    "collect_recent_egress_events",
]
