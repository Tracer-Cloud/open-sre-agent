"""Token meters for the monitor-local-agents fleet view.

Each meter is a tiny stateless parser that extracts a token count from
a chunk of CLI stdout. The dashboard wiring layer accumulates results
across chunks and divides by elapsed wall-clock time to produce the
``tokens/min`` and ``$/hr`` columns.

Cost calculation is deliberately not done here: per-token rates change
per model (Anthropic input/output rates, cache reads at 0.1×, cache
writes at 1.25×), so binding cost to the parser would couple
``tokens/min`` to ``$/hr`` in a way that grows brittle as models
multiply. Keep the parser dumb; let the dashboard do the math.
"""

from __future__ import annotations

from typing import Protocol


class TokenMeter(Protocol):
    """Protocol for a token-count parser over a CLI stdout chunk.

    Implementations must be safe to call with partial chunks — chunks
    coming from a streaming subprocess split at arbitrary byte offsets
    and may not align with line or JSON-document boundaries.
    """

    def parse_chunk(self, chunk: str) -> int:
        """Return the number of tokens reported in ``chunk``, or 0."""


class NullMeter:
    """Always returns 0; placeholder for providers without a real
    parser yet.

    Used by the registry's stub registrations for Codex, Cursor, Aider,
    Gemini, OpenCode, and Kimi until per-provider parsers ship.
    """

    def parse_chunk(self, chunk: str) -> int:
        del chunk
        return 0


null_meter: TokenMeter = NullMeter()


__all__ = ["NullMeter", "TokenMeter", "null_meter"]
