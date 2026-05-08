"""Token meter for Anthropic Claude Code CLI stdout.

Claude Code, when invoked with ``--output-format stream-json``, emits
NDJSON where each event may carry an Anthropic-shape ``usage`` block.
The dashboard wires this meter to claude-code processes; other
providers ship their own meters in follow-up issues.

The parser only counts ``input_tokens`` + ``output_tokens`` from
``type == "assistant"`` events. The CLI also emits a ``result`` event
at the end of every session whose ``usage`` block carries cumulative
output totals plus the final-turn input total — counting it would
overcount by ~50% in any multi-turn session. ``system``, ``user``,
and tool-result events have no usage block and are skipped.

Per-line JSON parsing rather than a free-form regex because the
``result``-vs-``assistant`` distinction is structural — discriminating
events by their ``type`` field is the only correct way. The wiring
layer in #1490 reads stdout line-by-line, so chunks delivered to this
parser are line-aligned. Partial lines are silently dropped on the
current call; the wiring layer is responsible for buffering across
chunk boundaries if its read strategy ever changes.
"""

from __future__ import annotations

import json
from typing import Any


class ClaudeCodeMeter:
    """Sums ``input_tokens`` + ``output_tokens`` from per-turn ``assistant``
    events in a Claude Code stream-json chunk.

    Cache-related counters (``cache_creation_input_tokens``,
    ``cache_read_input_tokens``) are intentionally not summed — they
    bill at 1.25× and 0.10× of the input rate respectively, so a
    ``$/hr`` calculation that lumps them with regular ``input_tokens``
    would be wrong. A follow-up issue can extend this parser to expose
    a structured ``TokenSample`` if the cache-cost breakdown becomes
    a hard requirement.
    """

    def parse_chunk(self, chunk: str) -> int:
        """Return ``input_tokens`` + ``output_tokens`` summed across the
        ``assistant`` events found in ``chunk``.

        Returns 0 for chunks that contain no parseable ``assistant``
        event (empty input, plain prose, the session-summary
        ``result`` event, ``system`` init, ``user`` tool results).
        """
        total = 0
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event: Any = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                continue
            total += _tokens_from_event(event)
        return total


def _tokens_from_event(event: object) -> int:
    """Extract ``input_tokens + output_tokens`` from one stream-json event.

    Anthropic-shape ``usage`` lives under ``message.usage`` for
    ``assistant`` events. Returns 0 for any event that doesn't match
    that shape, including the final ``result`` event whose totals are
    cumulative across the session.
    """
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return 0
    message = event.get("message")
    if not isinstance(message, dict):
        return 0
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0
    return _safe_int(usage.get("input_tokens")) + _safe_int(usage.get("output_tokens"))


def _safe_int(value: object) -> int:
    """Return ``int(value)`` if numeric and non-negative, else 0."""
    if isinstance(value, bool):
        # bool is a subclass of int; reject explicitly so a stray
        # ``"input_tokens": true`` in malformed output doesn't add 1.
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    return 0
