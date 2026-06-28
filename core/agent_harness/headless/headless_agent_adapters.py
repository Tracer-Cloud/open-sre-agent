"""In-memory implementations of the :mod:`core.agent_harness.ports` Protocols.

These let a turn run with no terminal: a buffer output sink, an in-memory
session store, empty grounding, no tools, and no analytics. They are the
concrete proof that the engine is decoupled from ``interactive_shell`` — an HTTP
handler or a test can drive a full turn with only a message and (optionally) a
reasoning client.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.ports import (
    ConfirmFn,
    ToolEventObserver,
)
from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult


@dataclass
class InMemorySessionStore:
    """List-backed :class:`core.agent_harness.ports.SessionStore` for headless runs."""

    session_id: str = "headless"
    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    configured_integrations: list[str] = field(default_factory=list)
    configured_integrations_known: bool = False
    last_state: dict[str, Any] | None = None
    last_synthetic_observation_path: str | None = None
    reasoning_effort: Any | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    last_command_observation: str | None = None
    resolved_integrations_cache: dict[str, Any] | None = None
    github_repo_scope: tuple[str, str] | None = None
    records: list[tuple[str, str, bool]] = field(default_factory=list)

    def record(self, kind: str, text: str, *, ok: bool = True) -> None:
        self.records.append((kind, text, ok))


@dataclass
class BufferOutputSink:
    """Collects all output into ``lines`` / ``streamed`` for inspection."""

    lines: list[str] = field(default_factory=list)
    streamed: list[str] = field(default_factory=list)

    def print(self, message: str = "") -> None:
        self.lines.append(message)

    def render_response_header(self, label: str) -> None:
        self.lines.append(f"[{label}]")

    def render_error(self, message: str) -> None:
        self.lines.append(f"ERROR: {message}")

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(str(chunk) for chunk in chunks)
        self.streamed.append(text)
        return text

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class EmptyPromptContextProvider:
    """Grounding provider that supplies no corpora (headless)."""

    def cli_reference(self) -> str:
        return ""

    def agents_md(self) -> str:
        return ""

    def investigation_flow(self) -> str:
        return ""

    def environment_block(self) -> str:
        return ""

    def suggested_synthetic_prompt(self) -> str:
        return ""

    def log_diagnostics(self, reason: str) -> None:
        _ = reason


class NullToolProvider:
    """Provides no action tools and a no-op tool-event observer."""

    def action_tools(self, *, confirm_fn: ConfirmFn | None, is_tty: bool | None) -> list[Any]:
        _ = (confirm_fn, is_tty)
        return []

    def observer(self, *, message: str) -> ToolEventObserver:
        _ = message

        def _observer(_kind: str, _data: dict[str, Any]) -> None:
            return None

        return _observer


class NoopTurnAccounting:
    """Records nothing and returns the result unchanged."""

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        _ = action_result

    def finalize(self, result: ShellTurnResult) -> ShellTurnResult:
        return result


class NoopErrorReporter:
    """Swallows reported exceptions (headless)."""

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        _ = (exc, context, expected)


@dataclass
class SimpleRunRecord:
    """Opaque conversational-LLM run record for headless runs."""

    response_text: str
    prompt: str = ""
    started: float = 0.0


class SimpleRunRecordFactory:
    """Builds :class:`SimpleRunRecord` values."""

    def build(
        self, *, client: Any, prompt: str, response_text: str, started: float
    ) -> SimpleRunRecord:
        _ = client
        return SimpleRunRecord(response_text=response_text, prompt=prompt, started=started)


@dataclass
class StaticReasoningClientProvider:
    """Provides a fixed reasoning client (or None to skip the assistant)."""

    client: Any | None = None

    def get(self) -> Any | None:
        return self.client


__all__ = [
    "BufferOutputSink",
    "EmptyPromptContextProvider",
    "InMemorySessionStore",
    "NoopErrorReporter",
    "NoopTurnAccounting",
    "NullToolProvider",
    "SimpleRunRecord",
    "SimpleRunRecordFactory",
    "StaticReasoningClientProvider",
]
