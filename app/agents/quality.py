"""Quality signals for monitored local agent streams (loop detection, etc.)."""

from __future__ import annotations

from collections import Counter, deque


class LoopDetector:
    """Rolling w-shingle detector for repetitive agent output.

    Ingests text chunks, tokenizes on whitespace (lowercased), and hashes
    consecutive token windows of size ``shingle_size``. The last ``window``
    emitted shingle hashes are retained; if any hash appears strictly more
    than ``threshold`` times in that deque, :attr:`is_looping` is true.

    Each logical shingle add or remove is O(1). :meth:`observe` costs
    O(number of tokens derived from *chunk*).
    """

    __slots__ = (
        "_pending",
        "_shingle_size",
        "_shingle_hashes",
        "_hash_count",
        "_threshold",
        "_violators",
    )

    def __init__(
        self,
        *,
        window: int = 20,
        threshold: int = 4,
        shingle_size: int = 3,
    ) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if threshold < 0:
            raise ValueError("threshold must be >= 0")
        if shingle_size < 1:
            raise ValueError("shingle_size must be >= 1")

        self._shingle_size = shingle_size
        self._threshold = threshold
        self._pending: deque[str] = deque(maxlen=shingle_size)
        self._shingle_hashes: deque[int] = deque(maxlen=window)
        self._hash_count: Counter[int] = Counter()
        self._violators: int = 0

    def clear(self) -> None:
        """Drop all buffered tokens, shingles, and counts."""
        self._pending.clear()
        self._shingle_hashes.clear()
        self._hash_count.clear()
        self._violators = 0

    def observe(self, chunk: str) -> None:
        """Feed a text fragment; updates shingles and loop state."""
        for token in self._tokens_from_chunk(chunk):
            self._pending.append(token)
            if len(self._pending) == self._shingle_size:
                shingle: tuple[str, ...] = tuple(self._pending)
                self._push_hash(hash(shingle))
                self._pending.popleft()

    @property
    def is_looping(self) -> bool:
        """True when some shingle hash count exceeds ``threshold`` in the rolling window."""
        return self._violators > 0

    def _tokens_from_chunk(self, chunk: str) -> list[str]:
        if not chunk or not chunk.strip():
            return []
        return [t for t in (p.lower() for p in chunk.split()) if t]

    def _push_hash(self, h: int) -> None:
        if len(self._shingle_hashes) == self._shingle_hashes.maxlen:
            evicted = self._shingle_hashes.popleft()
            self._decrement_hash(evicted)
        self._shingle_hashes.append(h)
        self._increment_hash(h)

    def _increment_hash(self, h: int) -> None:
        prev = self._hash_count[h]
        new = prev + 1
        self._hash_count[h] = new
        if prev <= self._threshold < new:
            self._violators += 1

    def _decrement_hash(self, h: int) -> None:
        prev = self._hash_count[h]
        new = prev - 1
        if new == 0:
            del self._hash_count[h]
        else:
            self._hash_count[h] = new
        if prev > self._threshold >= new:
            self._violators -= 1
