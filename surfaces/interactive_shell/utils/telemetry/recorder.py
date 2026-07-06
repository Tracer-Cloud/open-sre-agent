"""Prompt/response recorder for interactive-shell turns."""

from __future__ import annotations

import contextlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from config.version import get_version
from core.agent_harness.accounting.token_accounting import LlmRunInfo
from core.agent_harness.session.prompt_history.policy import redact_text
from platform.analytics.provider import JsonValue
from surfaces.interactive_shell.utils.telemetry.config import PromptLogConfig
from surfaces.interactive_shell.utils.telemetry.integration_snapshot import (
    build_turn_integration_snapshot,
)
from surfaces.interactive_shell.utils.telemetry.sinks.local_jsonl import (
    append_prompt_log_record,
)
from surfaces.interactive_shell.utils.telemetry.sinks.posthog_ai import capture_ai_generation

_SUPPORTED_TURN_KINDS = frozenset({"agent", "follow_up", "new_alert", "background_task"})

# Sentinel for turns handled by terminal tools/slash commands without the
# conversational assistant LLM (PostHog ``$ai_model`` / ``$ai_provider``).
NO_CONVERSATIONAL_AGENT = "no_conversational_agent"

# Maps PromptRecorder turn_kind to session turn kind stored in turn_detail records.
_TURN_TO_SESSION_KIND: dict[str, str] = {
    "agent": "chat",
    "follow_up": "follow_up",
    "new_alert": "alert",
    "background_task": "cli_command",
}


def _latest_slash_outcome(session: Any) -> str | None:
    history = getattr(session, "history", None)
    if not isinstance(history, list):
        return None
    for entry in reversed(history):
        if not isinstance(entry, dict) or entry.get("type") != "slash":
            continue
        outcome = entry.get("slash_outcome")
        if isinstance(outcome, str) and outcome:
            return outcome
        return None
    return None


def _fallback_terminal_response(*, prompt: str) -> str:
    stripped = prompt.strip()
    if stripped:
        return f"terminal turn handled: {stripped}"
    return "terminal turn handled"


def _prompt_relates_to_investigation(*, prompt: str, turn_kind: str) -> bool:
    stripped = prompt.strip().lower()
    if turn_kind == "background_task":
        return "investigate" in stripped
    return stripped.startswith("/investigate")


class PromptRecorder:
    """Captures one `(prompt, response)` pair and flushes to configured sinks."""

    def __init__(
        self,
        *,
        config: PromptLogConfig,
        turn_kind: str,
        session_id: str,
        turn_id: str,
        prompt: str,
        session: Any | None = None,
    ) -> None:
        self._config = config
        self._turn_kind = turn_kind
        self._session_id = session_id
        self._turn_id = turn_id
        self._prompt = prompt
        self._session = session
        self._response: str = ""
        self._scoped_investigation_id: str | None = None
        self._error_kind: str = ""
        self._error_message: str = ""
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
        turn_kind: str,
    ) -> PromptRecorder | None:
        config = PromptLogConfig.load()
        if not config.enabled or turn_kind not in _SUPPORTED_TURN_KINDS:
            # When prompt logging is fully disabled, no recorder is created and
            # no turn_detail records are written to the session file. This means
            # the crash-recovery fallback in load_session() will produce empty
            # cli_agent_messages for sessions that crashed before flush(). The
            # conversation_snapshot written at clean exit is unaffected.
            return None
        return cls(
            config=config,
            turn_kind=turn_kind,
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
        recorder = cls(
            config=config,
            turn_kind="background_task",
            session_id=_session_id(session),
            turn_id=task_id or str(uuid.uuid4()),
            prompt=_sanitize_text(command, config=config),
            session=session,
        )
        if _prompt_relates_to_investigation(prompt=command, turn_kind="background_task"):
            investigation_id = str(uuid.uuid4())
            recorder.bind_investigation_id(investigation_id)
            session.last_investigation_id = investigation_id
        return recorder

    def bind_investigation_id(self, investigation_id: str) -> None:
        cleaned = investigation_id.strip()
        if cleaned:
            self._scoped_investigation_id = cleaned

    def set_error(self, kind: str, message: str) -> None:
        """Attach a structured turn error emitted as ``$ai_error`` properties.

        The human-readable response text is unaffected; these properties make
        LLM/provider error detection exact instead of a regex over
        ``$ai_output_choices``.
        """
        kind = kind.strip()
        message = message.strip()
        if not (kind or message):
            return
        self._error_kind = kind or "error"
        self._error_message = _sanitize_text(message, config=self._config)

    def _resolve_investigation_id(self) -> str:
        if self._scoped_investigation_id:
            return self._scoped_investigation_id
        if self._session is None or not _prompt_relates_to_investigation(
            prompt=self._prompt,
            turn_kind=self._turn_kind,
        ):
            return ""
        investigation_id = getattr(self._session, "last_investigation_id", "")
        if isinstance(investigation_id, str):
            return investigation_id
        return ""

    def set_response(self, text: str, run: LlmRunInfo | None = None) -> None:
        cleaned = _sanitize_text(text, config=self._config)
        if not cleaned.strip():
            cleaned = _fallback_terminal_response(prompt=self._prompt)
        self._response = cleaned
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
            "turn_kind": self._turn_kind,
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
            from core.agent_harness.session import default_session_storage

            session_kind = _TURN_TO_SESSION_KIND.get(self._turn_kind, self._turn_kind)
            default_session_storage().append_turn_detail(
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
                posthog_properties: dict[str, JsonValue] = {
                    "$ai_trace_id": self._turn_id,
                    "$ai_session_id": self._session_id,
                    "$ai_span_id": self._turn_id,
                    "$ai_span_name": f"surfaces.interactive_shell.{self._turn_kind}",
                    "$ai_model": self._model or NO_CONVERSATIONAL_AGENT,
                    "$ai_provider": self._provider or NO_CONVERSATIONAL_AGENT,
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
                    "cli_turn_kind": self._turn_kind,
                    "cli_session_id": self._session_id,
                    "cli_turn_id": self._turn_id,
                    "opensre_version": get_version(),
                    **integration_snapshot,
                }
                slash_outcome = _latest_slash_outcome(self._session)
                if slash_outcome:
                    posthog_properties["slash_outcome"] = slash_outcome
                investigation_id = self._resolve_investigation_id()
                if investigation_id:
                    posthog_properties["investigation_id"] = investigation_id
                if self._error_kind:
                    posthog_properties["$ai_is_error"] = True
                    posthog_properties["$ai_error"] = self._error_message or self._error_kind
                    posthog_properties["error_kind"] = self._error_kind
                capture_ai_generation(posthog_properties)


def _sanitize_text(text: str, *, config: PromptLogConfig) -> str:
    if config.redact:
        text = redact_text(text)
    return text[: config.max_chars]


def _session_id(session: Any) -> str:
    # Prefer the stable first-class field set at Session construction.
    # Fall back to the legacy side-channel for non-Session callers.
    sid = getattr(session, "session_id", None) or getattr(session, "_prompt_log_session_id", None)
    if isinstance(sid, str) and sid:
        return sid
    sid = str(uuid.uuid4())
    with contextlib.suppress(AttributeError):
        session._prompt_log_session_id = sid
    return sid
