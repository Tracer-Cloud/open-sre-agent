"""Per-session token accounting, extracted from the session state object.

Groups the token-cost bookkeeping (``/cost``) into one cohesive value object so
the session state class does not carry accounting fields and methods directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    """Accumulated token counts and LLM call count for one session.

    ``totals`` keeps running sums under ``input`` / ``output`` plus the
    ``*_measured`` / ``*_estimated`` breakdown buckets. ``call_count`` is the
    number of recorded LLM calls (for ``/cost``).
    """

    totals: dict[str, int] = field(default_factory=dict)
    call_count: int = 0

    @property
    def has_estimates(self) -> bool:
        """True when any recorded tokens were estimated rather than measured."""
        return bool(self.totals.get("input_estimated") or self.totals.get("output_estimated"))

    def record(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated: bool = False,
    ) -> None:
        """Accumulate one LLM call's token counts (input/output + breakdown)."""
        if not input_tokens and not output_tokens:
            return
        suffix = "estimated" if estimated else "measured"
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if not count:
                continue
            self.totals[direction] = self.totals.get(direction, 0) + count
            bucket = f"{direction}_{suffix}"
            self.totals[bucket] = self.totals.get(bucket, 0) + count
        self.call_count += 1

    def reset(self) -> None:
        """Clear all accumulated counts (used by ``/new``)."""
        self.totals.clear()
        self.call_count = 0


__all__ = ["TokenUsage"]
