"""Per-category error/retry rate detector for monitored agent streams."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

_DEFAULT_WINDOW_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class ErrorCategory:
    name: str
    keywords: tuple[str, ...] = field(default=())
    patterns: tuple[re.Pattern[str], ...] = field(default=())


# Patterns require error-shape context (adjacent verb, status prefix, header
# form, etc.) so descriptive prose like "the OpenAI rate limit is 10k tokens"
# or "add tool error handling" does not fire. See #1497 acceptance criterion.
DEFAULT_CATEGORIES: tuple[ErrorCategory, ...] = (
    ErrorCategory(
        name="rate_limit",
        patterns=(
            re.compile(
                r"\brate[\s_-]?limit(?:ed|ing)?\b\s*"
                r"(?:exceeded|hit|reached|encountered|triggered|error|429)",
                re.IGNORECASE,
            ),
            re.compile(r"\bratelimit(?:ed|ing)\b", re.IGNORECASE),
            re.compile(
                r"\b429\b\s*(?::|-|—|too\s+many\s+requests|rate)",
                re.IGNORECASE,
            ),
        ),
    ),
    ErrorCategory(
        name="http_5xx",
        patterns=(
            re.compile(
                r"(?:"
                r"\bHTTP[/ ]\d(?:\.\d)?\s+5\d{2}\b"
                r"|\bstatus(?:\s*code)?[: ]+5\d{2}\b"
                r"|\b5\d{2}\s+(?:internal\s+server\s+error|bad\s+gateway|"
                r"service\s+unavailable|gateway\s+timeout|server\s+error)\b"
                r")",
                re.IGNORECASE,
            ),
        ),
    ),
    ErrorCategory(
        name="tool_failure",
        patterns=(
            re.compile(r"\btool[\s_-]?failure\b\s*[:\-—]\s*\S", re.IGNORECASE),
            re.compile(
                r"\btool\s+failed\b\s+(?:during|at|with|in|on|while|"
                r"after|when|to|for|because)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\btool\s+exited\s+with\s+(?:code\s+)?\d+", re.IGNORECASE),
        ),
    ),
    ErrorCategory(
        name="traceback",
        patterns=(re.compile(r"Traceback\s*\(most\s+recent\s+call\s+last\)"),),
    ),
)


class ErrorSignals:
    """Sliding-window error/retry rate tracker for an agent stdout stream.

    Thread-safe: a ``threading.Lock`` guards the prune-and-mutate sections
    of ``observe()`` and ``rate_per_minute()``. Regex matching runs outside
    the lock so a heavy chunk does not block the renderer thread.
    """

    __slots__ = (
        "_categories",
        "_window_seconds",
        "_now",
        "_events",
        "_lock",
        "_keyword_patterns",
    )

    def __init__(
        self,
        *,
        categories: Iterable[ErrorCategory] | None = None,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        now: Callable[[], float] | None = None,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        resolved = tuple(categories if categories is not None else DEFAULT_CATEGORIES)
        seen: set[str] = set()
        for cat in resolved:
            if cat.name in seen:
                raise ValueError(f"duplicate category name: {cat.name!r}")
            seen.add(cat.name)
        self._categories: tuple[ErrorCategory, ...] = resolved
        self._window_seconds = float(window_seconds)
        self._now = now if now is not None else time.monotonic
        self._events: dict[str, deque[float]] = {cat.name: deque() for cat in self._categories}
        self._lock = threading.Lock()
        # Pre-compile keywords with word boundaries so "error" does not match
        # "errored" / "errorless" / "noerror". Keeps custom-category keyword
        # use safe against adversarial substring inflation in agent stdout.
        self._keyword_patterns: dict[str, tuple[re.Pattern[str], ...]] = {
            cat.name: tuple(
                re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
                for kw in cat.keywords
                if kw
            )
            for cat in self._categories
        }

    def observe(self, chunk: str) -> None:
        if not chunk:
            return
        timestamp = self._now()
        cutoff = timestamp - self._window_seconds

        # Run regex matching outside the lock so a heavy chunk does not block
        # the renderer thread.
        hits_by_category: list[tuple[str, int]] = []
        for category in self._categories:
            hits = self._count_hits(category, chunk)
            if hits:
                hits_by_category.append((category.name, hits))

        # Prune in observe() too so an idle dashboard does not grow unbounded.
        with self._lock:
            for bucket in self._events.values():
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()
            for name, hits in hits_by_category:
                self._events[name].extend([timestamp] * hits)

    def rate_per_minute(self) -> dict[str, float]:
        cutoff = self._now() - self._window_seconds
        scale = 60.0 / self._window_seconds
        with self._lock:
            rates: dict[str, float] = {}
            for name, bucket in self._events.items():
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()
                rates[name] = len(bucket) * scale
        return rates

    def _count_hits(self, category: ErrorCategory, chunk: str) -> int:
        total = 0
        # Keywords match with word boundaries via pre-compiled patterns.
        for kw_pattern in self._keyword_patterns[category.name]:
            total += sum(1 for _ in kw_pattern.finditer(chunk))
        # finditer is unambiguous regardless of capturing-group structure;
        # findall would return group contents instead of full matches if a
        # future pattern adds a non-(?:...) group.
        for pattern in category.patterns:
            total += sum(1 for _ in pattern.finditer(chunk))
        return total
