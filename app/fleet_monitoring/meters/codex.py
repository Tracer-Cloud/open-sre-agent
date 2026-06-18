"""Token meter for OpenAI Codex CLI rollout NDJSON.

Verified empirically against codex-cli 0.130 rollouts. Event types
in ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl``:

- ``session_meta`` — once per session; carries cwd and
  ``model_provider`` but not a specific model.
- ``turn_context`` — once per turn; carries ``payload.model``.
- ``response_item`` — agent messages and tool calls; no usage.
- ``event_msg`` with ``payload.type == "token_count"`` — emitted
  after every turn; carries ``payload.info.last_token_usage`` with
  per-turn ``input_tokens``, ``cached_input_tokens``,
  ``output_tokens``, ``reasoning_output_tokens``. The first such
  event in a session carries ``info: null`` (rate-limit handshake).

This differs from ``codex exec --json`` stdout which uses
``turn.completed`` events. The on-disk rollout is the source of
truth because the wiring layer tails files, not stdout.

``cached_input_tokens`` is captured separately so pricing can apply
the discounted cache-read rate. ``total_token_usage`` is cumulative;
it is only used as a per-PID fallback by diffing against the previous
total observed for that PID.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from app.fleet_monitoring.meters import TokenSample, TokenUsage, safe_int


class CodexMeter:
    """Extracts per-turn usage from ``event_msg.token_count`` records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_usage_by_pid: dict[int, TokenUsage] = {}

    def parse_chunk(self, chunk: str) -> int:
        return self.sample_chunk(chunk).tokens

    def sample_chunk(self, chunk: str, *, pid: int | None = None) -> TokenSample:
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
            usage += self._usage_from_event(event, pid)
            event_model = _model_from_event(event)
            if event_model is not None:
                latest_model = event_model
        return TokenSample(usage=usage, model=latest_model)

    def forget(self, pid: int) -> None:
        with self._lock:
            self._total_usage_by_pid.pop(pid, None)

    def known_pids(self) -> list[int]:
        with self._lock:
            return list(self._total_usage_by_pid.keys())

    def _usage_from_event(self, event: object, pid: int | None) -> TokenUsage:
        info = _token_count_info(event)
        if info is None:
            return TokenUsage()

        last = info.get("last_token_usage")
        if isinstance(last, dict):
            if pid is not None:
                total = info.get("total_token_usage")
                if isinstance(total, dict):
                    with self._lock:
                        self._total_usage_by_pid[pid] = _usage_from_usage_dict(total)
            return _usage_from_usage_dict(last)

        total = info.get("total_token_usage")
        if pid is None or not isinstance(total, dict):
            return TokenUsage()

        current = _usage_from_usage_dict(total)
        with self._lock:
            previous = self._total_usage_by_pid.get(pid)
            self._total_usage_by_pid[pid] = current
        if previous is None or not _is_monotonic(previous, current):
            return TokenUsage()
        return _usage_delta(previous, current)


def _token_count_info(event: object) -> dict[str, Any] | None:
    if not isinstance(event, dict) or event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    return info


def _usage_from_usage_dict(raw: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=safe_int(raw.get("input_tokens")),
        output_tokens=safe_int(raw.get("output_tokens")),
        cached_input_tokens=safe_int(raw.get("cached_input_tokens")),
    )


def _is_monotonic(previous: TokenUsage, current: TokenUsage) -> bool:
    return (
        current.input_tokens >= previous.input_tokens
        and current.output_tokens >= previous.output_tokens
        and current.cached_input_tokens >= previous.cached_input_tokens
    )


def _usage_delta(previous: TokenUsage, current: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=current.input_tokens - previous.input_tokens,
        output_tokens=current.output_tokens - previous.output_tokens,
        cached_input_tokens=current.cached_input_tokens - previous.cached_input_tokens,
    )


def _model_from_event(event: object) -> str | None:
    if not isinstance(event, dict) or event.get("type") != "turn_context":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    if isinstance(model, str) and model:
        return model
    return None
