"""Prompt/response recorder for interactive-shell turns."""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.cli.interactive_shell.history.policy import redact_text
from app.cli.interactive_shell.prompt_logging.config import PromptLogConfig
from app.cli.interactive_shell.prompt_logging.sinks.local_jsonl import append_prompt_log_record
from app.cli.interactive_shell.prompt_logging.sinks.posthog_ai import capture_ai_generation
from app.version import get_version

_SUPPORTED_ROUTE_KINDS = frozenset({"cli_agent", "cli_help", "follow_up", "new_alert"})


@dataclass(frozen=True, slots=True)
class LlmRunInfo:
    """Best-effort metadata from one visible LLM response."""

    model: str | None = None
    provider: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    response_text: str | None = None


class PromptRecorder:
    """Captures one `(prompt, response)` pair and flushes to configured sinks."""

    def __init__(
        self,
        *,
        config: PromptLogConfig,
        route_kind: str,
        session_id: str,
        turn_id: str,
        prompt: str,
    ) -> None:
        self._config = config
        self._route_kind = route_kind
        self._session_id = session_id
        self._turn_id = turn_id
        self._prompt = prompt
        self._response: str = ""
        self._model: str | None = None
        self._provider: str | None = None
        self._latency_ms: int | None = None
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._start = time.monotonic()
        self._flushed = False

    @classmethod
    def start(
        cls,
        *,
        session: Any,
        text: str,
        route_kind: str,
    ) -> PromptRecorder | None:
        config = PromptLogConfig.load()
        if not config.enabled or route_kind not in _SUPPORTED_ROUTE_KINDS:
            return None
        return cls(
            config=config,
            route_kind=route_kind,
            session_id=_session_id(session),
            turn_id=str(uuid.uuid4()),
            prompt=_sanitize_text(text, config=config),
        )

    def set_response(self, text: str, run: LlmRunInfo | None = None) -> None:
        self._response = _sanitize_text(text, config=self._config)
        if run is None:
            self._latency_ms = int((time.monotonic() - self._start) * 1000)
            return
        self._model = run.model
        self._provider = run.provider
        self._latency_ms = run.latency_ms or int((time.monotonic() - self._start) * 1000)
        self._input_tokens = run.input_tokens
        self._output_tokens = run.output_tokens

    def flush(self) -> None:
        if self._flushed:
            return
        self._flushed = True
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": self._session_id,
            "turn_id": self._turn_id,
            "route_kind": self._route_kind,
            "prompt": self._prompt,
            "response": self._response,
            "model": self._model or "",
            "provider": self._provider or "",
            "latency_ms": self._latency_ms or int((time.monotonic() - self._start) * 1000),
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "opensre_version": get_version(),
        }
        if self._config.local_enabled:
            with contextlib.suppress(OSError):
                append_prompt_log_record(path=self._config.log_path, record=record)
        if self._config.posthog_enabled:
            with contextlib.suppress(Exception):
                capture_ai_generation(
                    {
                        "$ai_trace_id": self._turn_id,
                        "$ai_session_id": self._session_id,
                        "$ai_span_id": self._turn_id,
                        "$ai_span_name": f"interactive_shell.{self._route_kind}",
                        "$ai_model": self._model or "unknown",
                        "$ai_provider": self._provider or "unknown",
                        "$ai_input": [{"role": "user", "content": self._prompt}],
                        "$ai_output_choices": [
                            {
                                "role": "assistant",
                                "content": self._response,
                            }
                        ],
                        "$ai_latency": (
                            round((self._latency_ms or 0) / 1000.0, 3) if self._latency_ms else 0.0
                        ),
                        "$ai_input_tokens": self._input_tokens or 0,
                        "$ai_output_tokens": self._output_tokens or 0,
                        "cli_route_kind": self._route_kind,
                        "cli_session_id": self._session_id,
                        "cli_turn_id": self._turn_id,
                        "opensre_version": get_version(),
                    }
                )


def _sanitize_text(text: str, *, config: PromptLogConfig) -> str:
    if config.redact:
        text = redact_text(text)
    return text[: config.max_chars]


def _session_id(session: Any) -> str:
    value = getattr(session, "_prompt_log_session_id", None)
    if isinstance(value, str) and value:
        return value
    session_id = str(uuid.uuid4())
    session._prompt_log_session_id = session_id
    return session_id
