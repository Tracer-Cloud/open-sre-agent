"""Token-count constants and short-form formatter.

Shared between the streaming renderer (ui/streaming.py) and the spinner state
(runtime/state.py) so both display the same ``1.2k`` format and the same
chars-per-token heuristic.
"""

from __future__ import annotations

# Approximate characters per token used to estimate token counts from byte
# lengths without waiting for the API to return exact usage.
_CHARS_PER_TOKEN = 4


def format_token_count_short(token_count: int) -> str:
    """Format a token count as a short string — ``42`` / ``1.2k`` / ``1.0M``."""
    if token_count < 1000:
        return str(token_count)
    # Branch on the rounded-to-one-decimal magnitude, not the raw value: a
    # count like 999_950 rounds to "1000.0" at .1f precision, and comparing
    # the raw value against 1_000_000 let that render as the broken "1000.0k"
    # instead of rolling into the M branch below.
    if round(token_count / 1000, 1) < 1000:
        return f"{token_count / 1000:.1f}k"
    return f"{token_count / 1_000_000:.1f}M"


__all__ = ["_CHARS_PER_TOKEN", "format_token_count_short"]
