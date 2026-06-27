"""Durable shell agent for interactive-shell prompts.

``ShellAgent`` is the shell-facing agent object. It owns the live session,
lifecycle state, active-prompt guard, event subscribers, and injected runtime
primitives. Per-prompt action/answer mechanics live in ``agent_loop.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

from rich.console import Console

from interactive_shell.harness.agent_loop import (
    GatherEvidence,
    ResponseGenerator,
    RunToolCallingTurn,
    run_agent_prompt,
)
from interactive_shell.harness.events import AgentEvent, AgentEventSink
from interactive_shell.harness.llm_context.session import ReplSession
from interactive_shell.harness.state import ConversationState
from interactive_shell.runtime.core.confirmation import DispatchCancelled
from interactive_shell.runtime.core.turn_accounting import ShellTurnResult
from interactive_shell.utils.telemetry import PromptRecorder


class ShellAgent:
    """Stateful owner of the interactive-shell agent lifecycle.

    The shell agent owns shell/session state, event subscribers, lifecycle
    state, and active prompt execution. It delegates one prompt's action/answer
    loop to ``run_agent_prompt``. That loop may use ``core.runtime.agent.Agent``
    through ``tool_calling.py`` as a disposable tool-calling primitive.
    """

    def __init__(
        self,
        session: ReplSession,
        *,
        execute_actions: RunToolCallingTurn | None = None,
        gather_evidence: GatherEvidence | None = None,
        response_generator: ResponseGenerator | None = None,
        event_sink: AgentEventSink | None = None,
    ) -> None:
        self.session = session
        self._execute_actions = execute_actions
        self._gather_evidence = gather_evidence
        self._response_generator = response_generator
        self._event_sinks: list[AgentEventSink] = []
        self._started = False
        self._active_prompt: asyncio.Task[ShellTurnResult] | None = None
        if event_sink is not None:
            self.subscribe(event_sink)

    @property
    def state(self) -> ConversationState:
        """Return the shell-owned conversational state for this session."""
        return self.session.agent

    @property
    def started(self) -> bool:
        """Whether the shell agent has been started and not yet stopped."""
        return self._started

    @property
    def active(self) -> bool:
        """Whether a prompt is currently running."""
        return self._active_prompt is not None and not self._active_prompt.done()

    def subscribe(self, sink: AgentEventSink) -> Callable[[], None]:
        """Subscribe to lifecycle events and return an unsubscribe callback."""
        self._event_sinks.append(sink)

        def _unsubscribe() -> None:
            with suppress(ValueError):
                self._event_sinks.remove(sink)

        return _unsubscribe

    def start(self) -> None:
        """Start the shell agent lifecycle."""
        if self._started:
            return
        self._started = True
        self._emit(AgentEvent(type="agent_start"))

    async def prompt(
        self,
        text: str,
        *,
        console: Console,
        recorder: PromptRecorder | None,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
    ) -> ShellTurnResult:
        """Run one submitted user prompt through the shell agent."""
        if not self._started:
            raise RuntimeError("ShellAgent.start() must be called before prompt().")
        if self.active:
            raise RuntimeError("ShellAgent is already processing a prompt.")

        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("ShellAgent.prompt() requires a running asyncio task.")
        self._active_prompt = task  # type: ignore[assignment]

        try:
            return await asyncio.to_thread(
                self._run_prompt_lifecycle,
                text,
                console=console,
                recorder=recorder,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )
        finally:
            self._active_prompt = None

    def abort(self) -> None:
        """Cancel the active prompt task, if one is running."""
        if self._active_prompt is not None and not self._active_prompt.done():
            self._active_prompt.cancel()

    async def wait_idle(self) -> None:
        """Wait until the active prompt task finishes."""
        task = self._active_prompt
        if task is None or task is asyncio.current_task():
            return
        with suppress(asyncio.CancelledError):
            await task

    async def stop(self) -> None:
        """Stop the shell agent lifecycle after any active prompt settles."""
        await self.wait_idle()
        if not self._started:
            return
        self._started = False
        self._emit(AgentEvent(type="agent_stop"))

    def _run_prompt_lifecycle(
        self,
        text: str,
        *,
        console: Console,
        recorder: PromptRecorder | None,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
    ) -> ShellTurnResult:
        """Run the sync prompt loop and emit lifecycle events."""
        self._emit(AgentEvent(type="prompt_start", text=text))
        try:
            return run_agent_prompt(
                text,
                session=self.session,
                console=console,
                recorder=recorder,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                execute_actions=self._execute_actions,
                gather_evidence=self._gather_evidence,
                response_generator=self._response_generator,
            )
        except DispatchCancelled:
            self._emit(AgentEvent(type="prompt_interrupted"))
            raise
        except Exception as exc:
            self._emit(AgentEvent(type="prompt_error", error=exc))
            raise
        finally:
            self._emit(AgentEvent(type="prompt_end"))

    def _emit(self, event: AgentEvent) -> None:
        for sink in tuple(self._event_sinks):
            sink(event)


__all__ = [
    "ShellAgent",
]
