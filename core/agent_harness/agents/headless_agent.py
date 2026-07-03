"""Headless programmatic entry point and in-memory port adapters.

This is the proof that the agent is decoupled from any terminal: a caller (an
HTTP handler, a script, a test) can run a full turn with only a message. All the
surface concerns are satisfied by the in-memory adapters below, but every
dependency is injectable so a real surface can override any of them.

Example::

    from core.agent_harness.agents.headless_agent import (
        dispatch_message_to_headless_agent,
        InMemorySessionStore,
        NullToolProvider,
        StaticReasoningClientProvider,
    )

    class _Echo:
        def invoke_stream(self, prompt):
            yield "hello"

    result = dispatch_message_to_headless_agent(
        "hi there",
        tools=NullToolProvider(),
        reasoning=StaticReasoningClientProvider(client=_Echo()),
    )
    print(result.assistant_response_text)  # -> "hello"
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.agents.action_agent import run_action_agent_turn
from core.agent_harness.agents.evidence_agent import gather_tool_evidence
from core.agent_harness.agents.turn_orchestrator import run_turn, stream_answer
from core.agent_harness.models.turn_context import TurnContext
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    SessionStore,
    ToolEventObserver,
    ToolProvider,
    TurnAccounting,
)
from core.agent_harness.providers.default_prompt_context import (
    DefaultPromptContextProvider,
    supports_default_prompt_context,
)
from core.agent_harness.providers.default_providers import DefaultTurnAccounting
from core.execution import ToolExecutionHooks


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

    def tool_resources(self) -> dict[str, Any]:
        return {}

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


def dispatch_message_to_headless_agent(
    message: str,
    *,
    tools: ToolProvider,
    session: SessionStore | None = None,
    output: OutputSink | None = None,
    prompts: PromptContextProvider | None = None,
    reasoning: ReasoningClientProvider | None = None,
    run_factory: RunRecordFactory | None = None,
    accounting: TurnAccounting | None = None,
    error_reporter: ErrorReporter | None = None,
    gather_enabled: bool = False,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ShellTurnResult:
    """Run one full turn headlessly and return the :class:`ShellTurnResult`.

    ``tools`` is required. A surface that genuinely wants a text-only turn
    passes :class:`NullToolProvider` explicitly. Every other port defaults to
    an in-memory headless adapter. ``reasoning`` defaults to "no client" (the
    conversational assistant is skipped) so a turn runs with zero
    configuration; inject a client to get an actual answer. ``gather_enabled``
    turns on the live evidence-gather pass (off by default, since it reaches
    out to integrations).
    """
    store: SessionStore = session if session is not None else InMemorySessionStore()
    output = output if output is not None else BufferOutputSink()
    prompts = (
        prompts
        if prompts is not None
        else (
            DefaultPromptContextProvider(store)
            if supports_default_prompt_context(store)
            else EmptyPromptContextProvider()
        )
    )
    reasoning = reasoning if reasoning is not None else StaticReasoningClientProvider()
    run_factory = run_factory if run_factory is not None else SimpleRunRecordFactory()
    accounting = (
        accounting
        if accounting is not None
        else (
            DefaultTurnAccounting(store, message)
            if hasattr(store, "storage")
            else NoopTurnAccounting()
        )
    )
    error_reporter = error_reporter if error_reporter is not None else NoopErrorReporter()

    def execute_actions(
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_ctx: TurnContext | None = None,
    ) -> ToolCallingTurnResult:
        return run_action_agent_turn(
            text,
            store,
            output=output,
            tools=tools,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            turn_ctx=turn_ctx,
            error_reporter=error_reporter,
            tool_hooks=tool_hooks,
        )

    def answer(text: str, **kwargs: object) -> object:
        return stream_answer(
            text,
            store,
            output,
            prompts=prompts,
            reasoning=reasoning,
            run_factory=run_factory,
            error_reporter=error_reporter,
            **kwargs,  # type: ignore[arg-type]
        )

    def gather(text: str, *, is_tty: bool | None = None) -> str | None:
        if not gather_enabled:
            return None
        return gather_tool_evidence(
            text,
            store,
            error_reporter=error_reporter,
            is_tty=is_tty,
        )

    return run_turn(
        message,
        store,
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=accounting,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )


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
    "dispatch_message_to_headless_agent",
]
