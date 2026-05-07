"""Token meter for Anthropic Claude Code CLI stdout.

Claude Code, when invoked with ``--output-format stream-json``, emits
NDJSON where each event may carry an Anthropic-shape ``usage`` block
with ``input_tokens`` and ``output_tokens`` counters. The dashboard
wires this meter to claude-code processes; other providers ship their
own meters in follow-up issues.

Why a regex over the JSON keys rather than ``json.loads`` per line:
chunks delivered by a streaming subprocess pipe are byte-aligned, not
line-aligned, so a chunk may end mid-token-string. The regex matches
on the quoted JSON-key shape (``"input_tokens": 50``) which is robust
to that splitting — at worst a number split across chunks is missed
on this call, picked up implicitly when the next chunk arrives because
the surrounding text gets re-scanned by the wiring layer's accumulator.
"""

from __future__ import annotations

import re

# Matches Anthropic-style usage fields in the streaming JSON output.
# Quoted JSON-key form rather than free-form "tokens" mentions to
# avoid false positives if a Claude Code response happens to contain
# the word in a code block or assistant message body.
_TOKEN_RE = re.compile(r'"(?:input_tokens|output_tokens)"\s*:\s*(\d+)')


class ClaudeCodeMeter:
    """Sums ``input_tokens`` + ``output_tokens`` reported in a chunk.

    Cache-related counters (``cache_creation_input_tokens``,
    ``cache_read_input_tokens``) are intentionally not summed here —
    they're billed at different rates and the dashboard's ``$/hr``
    column needs them broken out. A follow-up issue can extend this
    parser to expose a structured ``TokenSample`` if the cache-cost
    breakdown becomes a hard requirement.
    """

    def parse_chunk(self, chunk: str) -> int:
        """Return total ``input_tokens + output_tokens`` found in ``chunk``."""
        return sum(int(match) for match in _TOKEN_RE.findall(chunk))
