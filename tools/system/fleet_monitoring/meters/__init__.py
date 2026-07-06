"""Token meters: stateless parsers extracting token counts from CLI output.

Cost calculation is deliberately separate — per-token rates change
per model and per counter (cache reads at 0.1×, cache writes at
1.25×), so binding cost to the parser would couple ``tokens/min``
to ``$/hr`` in a way that grows brittle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class TokenUsage:
    """Structured token counters extracted from one meter sample.

    ``cached_input_tokens`` is a discounted subset of ``input_tokens``
    for Codex/OpenAI-style usage. Claude's cache counters are emitted
    as separate read/write fields and are additive for display.
    """

    input_tokens: float = 0
    output_tokens: float = 0
    cached_input_tokens: float = 0
    cache_read_input_tokens: float = 0
    cache_creation_input_tokens: float = 0

    @classmethod
    def from_total(cls, tokens: float) -> TokenUsage:
        return cls(input_tokens=max(0.0, tokens))

    @property
    def tokens(self) -> float:
        """Visible total for the dashboard's ``tokens/min`` cell."""
        return (
            max(0.0, self.input_tokens)
            + max(0.0, self.output_tokens)
            + max(0.0, self.cache_read_input_tokens)
            + max(0.0, self.cache_creation_input_tokens)
        )

    def clamped(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=max(0.0, self.input_tokens),
            output_tokens=max(0.0, self.output_tokens),
            cached_input_tokens=max(0.0, self.cached_input_tokens),
            cache_read_input_tokens=max(0.0, self.cache_read_input_tokens),
            cache_creation_input_tokens=max(0.0, self.cache_creation_input_tokens),
        )

    def scaled(self, factor: float) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens * factor,
            output_tokens=self.output_tokens * factor,
            cached_input_tokens=self.cached_input_tokens * factor,
            cache_read_input_tokens=self.cache_read_input_tokens * factor,
            cache_creation_input_tokens=self.cache_creation_input_tokens * factor,
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )


@dataclass(frozen=True)
class TokenSample:
    """A meter's read of a stdout chunk: usage buckets + optional model hint."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None

    @classmethod
    def from_tokens(cls, tokens: float, model: str | None = None) -> TokenSample:
        return cls(usage=TokenUsage.from_total(tokens).clamped(), model=model)

    @property
    def tokens(self) -> int:
        return int(self.usage.tokens)


class TokenMeter(Protocol):
    """A token-count parser over a CLI stdout chunk.

    Implementations must be safe to call with partial chunks — chunks
    coming from a streaming subprocess split at arbitrary byte offsets
    and may not align with line or JSON-document boundaries.
    """

    def parse_chunk(self, chunk: str, /) -> int:
        """Return newly observed token count from ``chunk``; must not mutate any per-PID parser state."""
        raise NotImplementedError

    def sample_chunk(self, chunk: str, /, *, pid: int | None = None) -> TokenSample:
        """Return parsed usage/model data for ``chunk`` and optional ``pid`` without negative counts."""
        raise NotImplementedError

    def forget(self, pid: int, /) -> None:
        """Drop any parser state for ``pid`` so future samples start from a clean stream boundary."""
        raise NotImplementedError

    def known_pids(self) -> list[int]:
        """Return PIDs with retained parser state; callers may use this for cleanup bookkeeping."""
        raise NotImplementedError


class NullMeter:
    """Always returns 0 / ``None``."""

    def parse_chunk(self, _chunk: str, /) -> int:
        return 0

    def sample_chunk(self, _chunk: str, /, *, pid: int | None = None) -> TokenSample:  # noqa: ARG002
        return TokenSample()

    def forget(self, _pid: int, /) -> None:
        return None

    def known_pids(self) -> list[int]:
        return []


null_meter: TokenMeter = NullMeter()


def safe_int(value: object) -> int:
    """Coerce ``value`` to a non-negative int.

    ``bool`` is rejected explicitly because ``isinstance(True, int)``
    is ``True`` — a stray ``"input_tokens": true`` must not add 1.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    return 0


__all__ = [
    "NullMeter",
    "TokenMeter",
    "TokenSample",
    "TokenUsage",
    "null_meter",
    "safe_int",
]
