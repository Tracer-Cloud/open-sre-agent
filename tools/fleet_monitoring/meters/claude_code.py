"""Token meter for Anthropic Claude Code stream-json stdout.

Claude Code with ``--output-format stream-json`` emits NDJSON where
each ``assistant`` event carries an Anthropic-shape ``usage`` block
under ``message.usage``. The ``result`` event at session end carries
cumulative totals — counting it would overcount by ~50% in any
multi-turn session.

Cache counters (``cache_creation_input_tokens``,
``cache_read_input_tokens``) are returned separately so pricing can
apply the right per-bucket rates.
"""

from __future__ import annotations

import json
from typing import Any

from tools.fleet_monitoring.meters import TokenSample, TokenUsage, safe_int


class ClaudeCodeMeter:
    """Extracts token usage from ``assistant`` events."""

    def parse_chunk(self, chunk: str) -> int:
        return self.sample_chunk(chunk).tokens

    def sample_chunk(self, chunk: str, *, pid: int | None = None) -> TokenSample:  # noqa: ARG002
        usage = TokenUsage()
        latest_model: str | None = None
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event: Any = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                continue
            usage += _usage_from_event(event)
            event_model = _model_from_event(event)
            if event_model is not None:
                latest_model = event_model
        return TokenSample(usage=usage, model=latest_model)

    def forget(self, _pid: int) -> None:
        return None

    def known_pids(self) -> list[int]:
        return []


def _usage_from_event(event: object) -> TokenUsage:
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return TokenUsage()
    message = event.get("message")
    if not isinstance(message, dict):
        return TokenUsage()
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    return TokenUsage(
        input_tokens=safe_int(usage.get("input_tokens")),
        output_tokens=safe_int(usage.get("output_tokens")),
        cache_read_input_tokens=safe_int(usage.get("cache_read_input_tokens")),
        cache_creation_input_tokens=safe_int(usage.get("cache_creation_input_tokens")),
    )


def _model_from_event(event: object) -> str | None:
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    model = message.get("model")
    if isinstance(model, str) and model:
        return model
    return None
