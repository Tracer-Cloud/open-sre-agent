"""Prompt/response recorder for interactive-shell turns."""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cli.interactive_shell.history.policy import redact_text
from cli.interactive_shell.prompt_logging.config import PromptLogConfig
from cli.interactive_shell.prompt_logging.integration_snapshot import (
    build_turn_integration_snapshot,
)
from cli.interactive_shell.prompt_logging.sinks.local_jsonl import append_prompt_log_record
from cli.interactive_shell.prompt_logging.sinks.posthog_ai import capture_ai_generation
from config.version import get_version

_SUPPORTED_ROUTE_KINDS = frozenset(
    {"handle_message_with_agent", "cli_help", "follow_up", "new_alert", "background_task"}
)

# Maps PromptRecorder route_kind → session turn kind stored in turn_detail records.
_ROUTE_TO_SESSION_KIND: dict[str, str] = {
    "handle_message_with_agent": "chat",
    "cli_help": "chat",
    "follow_up": "follow_up",
    "new_alert": "alert",
    "background_task": "cli_command",
}


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
        session: Any | None = None,
    ) -> None:
        self._config = config
        self._route_kind = route_kind
        self._session_id = session_id
        self._turn_id = turn_id
        self._prompt = prompt
        self._session = session
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
            # When prompt logging is fully disabled, no recorder is created and
            # no turn_detail records are written to the session file. This means
            # the crash-recovery fallback in load_session() will produce empty
            # cli_agent_messages for sessions that crashed before flush(). The
            # conversation_snapshot written at clean exit is unaffected.
            return None
        return cls(
            config=config,
            route_kind=route_kind,
            session_id=_session_id(session),
            turn_id=str(uuid.uuid4()),
            prompt=_sanitize_text(text, config=config),
            session=session,
        )

    @classmethod
    def for_background_task(
        cls,
        *,
        session: Any,
        command: str,
        task_id: str,
    ) -> PromptRecorder | None:
        """Create a recorder for an async background task.

        Background CLI tasks (e.g. ``opensre investigate``) finish long after
        the originating turn has flushed, so their stdout/stderr/exit outcome is
        not available to the turn-level recorder. This recorder is created at
        task launch — so its latency clock spans the full task duration — and is
        flushed by the task watcher once the outcome (including any error text)
        is known. ``turn_id`` is set to ``task_id`` so the prompt-log event
        correlates with the task surfaced by ``/tasks``.
        """
        config = PromptLogConfig.load()
        if not config.enabled:
            return None
        return cls(
            config=config,
            route_kind="background_task",
            session_id=_session_id(session),
            turn_id=task_id or str(uuid.uuid4()),
            prompt=_sanitize_text(command, config=config),
            session=session,
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
        latency_ms = self._latency_ms or int((time.monotonic() - self._start) * 1000)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": self._session_id,
            "turn_id": self._turn_id,
            "route_kind": self._route_kind,
            "prompt": self._prompt,
            "response": self._response,
            "model": self._model or "",
            "provider": self._provider or "",
            "latency_ms": latency_ms,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "opensre_version": get_version(),
        }
        if self._config.local_enabled:
            with contextlib.suppress(OSError):
                append_prompt_log_record(path=self._config.log_path, record=record)

        # Also write enriched turn to the session file so /resume can restore context.
        with contextlib.suppress(Exception):
            from cli.interactive_shell.sessions.store import SessionStore

            session_kind = _ROUTE_TO_SESSION_KIND.get(self._route_kind, self._route_kind)
            SessionStore.append_turn_detail(
                self._session_id,
                session_kind,
                self._prompt,
                response=self._response or None,
                turn_id=self._turn_id,
                model=self._model or None,
                provider=self._provider or None,
                latency_ms=latency_ms,
            )

        if self._config.posthog_enabled:
            with contextlib.suppress(Exception):
                integration_snapshot = build_turn_integration_snapshot(self._session)
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
                        **integration_snapshot,
                    }
                )


def _sanitize_text(text: str, *, config: PromptLogConfig) -> str:
    if config.redact:
        text = redact_text(text)
    return text[: config.max_chars]


def _session_id(session: Any) -> str:
    # Prefer the stable first-class field set at ReplSession construction.
    # Fall back to the legacy side-channel for non-ReplSession callers.
    sid = getattr(session, "session_id", None) or getattr(session, "_prompt_log_session_id", None)
    if isinstance(sid, str) and sid:
        return sid
    sid = str(uuid.uuid4())
    with contextlib.suppress(AttributeError):
        session._prompt_log_session_id = sid
    return sid
