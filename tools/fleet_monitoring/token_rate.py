"""Per-PID rolling token-rate tracker.

The dashboard's ``tokens/min`` cell is a *rate*, not a cumulative
count: each row shows tokens emitted over the last 60 s. Meters
parse a chunk into a count; this tracker accumulates per-PID into a
sliding window.

Concurrency: the sampler writes from an asyncio executor while the
view reads from a worker thread. A single ``threading.Lock`` guards
the inner dict.

Time source: ``time.monotonic()``. ``time.time()`` would let an NTP
step or manual clock adjustment evict the entire window (jump
forward) or freeze it (jump backward).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import ClassVar

from tools.fleet_monitoring.meters import TokenUsage


@dataclass(frozen=True)
class _TokenEntry:
    timestamp: float
    usage: TokenUsage
    model: str | None


class TokenRateTracker:
    """Per-PID rolling 60 s window of token counts.

    Returns ``None`` for never-recorded PIDs (renders ``-``) and
    ``0.0`` for recorded-but-idle PIDs (renders ``0``).
    """

    WINDOW_SECONDS: ClassVar[float] = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_pid: dict[int, deque[_TokenEntry]] = defaultdict(deque)
        # Separate from ``_per_pid`` so a long-idle session keeps a
        # known model after the 60s window evicts all its token
        # entries. Cleared only on ``forget``.
        self._latest_model: dict[int, str] = {}

    def record(
        self,
        pid: int,
        tokens: float | None = None,
        *,
        usage: TokenUsage | None = None,
        model: str | None = None,
    ) -> None:
        """Append an observation. Eviction of stale entries happens here only."""
        if usage is None:
            usage = TokenUsage.from_total(0.0 if tokens is None else tokens)
        usage = usage.clamped()
        now = time.monotonic()
        with self._lock:
            entries = self._per_pid[pid]
            entries.append(_TokenEntry(timestamp=now, usage=usage, model=model))
            if model is not None:
                self._latest_model[pid] = model
            self._evict_old(entries, now)

    def tokens_per_min(self, pid: int) -> float | None:
        """Return tokens summed over the trailing ``WINDOW_SECONDS``.

        Returns ``None`` only when ``pid`` has never been recorded.
        The integer sum becomes the per-minute figure because the
        window is 60 s; the general formula scales by
        ``60 / WINDOW_SECONDS`` so tests can shrink the window
        without breaking unit semantics.
        """
        now = time.monotonic()
        with self._lock:
            entries = self._per_pid.get(pid)
            if entries is None:
                return None
            # Reader-side eviction so a long-idle PID's stale window
            # never renders as a non-zero rate before the next write.
            self._evict_old(entries, now)
            total = _sum_usage(entries)
        return total.tokens * (60.0 / self.WINDOW_SECONDS)

    def usage_per_min(self, pid: int) -> TokenUsage | None:
        """Return structured usage summed over the trailing window."""
        now = time.monotonic()
        with self._lock:
            entries = self._per_pid.get(pid)
            if entries is None:
                return None
            self._evict_old(entries, now)
            total = _sum_usage(entries)
        return total.scaled(60.0 / self.WINDOW_SECONDS)

    def latest_model(self, pid: int) -> str | None:
        """Return the most recent non-``None`` model observed for ``pid``."""
        with self._lock:
            return self._latest_model.get(pid)

    def forget(self, pid: int) -> None:
        with self._lock:
            self._per_pid.pop(pid, None)
            self._latest_model.pop(pid, None)

    def known_pids(self) -> list[int]:
        with self._lock:
            return list(self._per_pid.keys())

    @classmethod
    def _evict_old(cls, entries: deque[_TokenEntry], now: float) -> None:
        cutoff = now - cls.WINDOW_SECONDS
        while entries and entries[0].timestamp < cutoff:
            entries.popleft()


TOKEN_RATE_TRACKER = TokenRateTracker()


__all__ = ["TOKEN_RATE_TRACKER", "TokenRateTracker"]


def _sum_usage(entries: deque[_TokenEntry]) -> TokenUsage:
    total = TokenUsage()
    for entry in entries:
        total += entry.usage
    return total
