"""Headless programmatic entry point and in-memory port adapters.

This is the proof that the agent is decoupled from any terminal: a caller (an
HTTP handler, a script, a test) can run a full turn with only a message. All the
surface concerns are satisfied by the in-memory adapters below, but every
dependency is injectable so a real surface can override any of them.

Example::

    from core.agent_harness.turns.headless_dispatch import (
        HeadlessAgent,
        InMemorySessionStore,
        NullToolProvider,
        StaticReasoningClientProvider,
    )

    class _Echo:
        def invoke_stream(self, prompt):
            yield "hello"

    agent = HeadlessAgent(
        tools=NullToolProvider(),
        reasoning=StaticReasoningClientProvider(client=_Echo()),
    )
    result = agent.dispatch("hi there")
    print(result.assistant_response_text)  # -> "hello"
"""

from __future__ import annotations

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    SessionStore,
    ToolProvider,
    TurnAccounting,
)
from core.agent_harness.prompts.prompt_context import (
    DefaultPromptContextProvider,
    supports_default_prompt_context,
)
from core.agent_harness.turns.action_driver import run_action_agent_turn
from core.agent_harness.turns.evidence_driver import gather_tool_evidence
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionStore,
    NoopErrorReporter,
    NoopTurnAccounting,
    NullToolProvider,
    SimpleRunRecord,
    SimpleRunRecordFactory,
    StaticReasoningClientProvider,
)
from core.agent_harness.turns.orchestrator import run_turn, stream_answer
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.execution import ToolExecutionHooks


class HeadlessAgent:
    """Runs full agent turns headlessly from a fixed set of configured ports.

    Construct once with the ports/dependencies, then call :meth:`dispatch` per
    message. ``tools`` is required — a surface that genuinely wants a text-only
    turn passes :class:`NullToolProvider` explicitly. Every other port defaults
    to an in-memory headless adapter. ``reasoning`` defaults to "no client" (the
    conversational assistant is skipped) so a turn runs with zero configuration;
    inject a client to get an actual answer. ``gather_enabled`` turns on the live
    evidence-gather pass (off by default, since it reaches out to integrations).
    """

    def __init__(
        self,
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
    ) -> None:
        self._tools = tools
        self._store: SessionStore = session if session is not None else InMemorySessionStore()
        self._output: OutputSink = output if output is not None else BufferOutputSink()
        self._prompts: PromptContextProvider = (
            prompts
            if prompts is not None
            else (
                DefaultPromptContextProvider(self._store)
                if supports_default_prompt_context(self._store)
                else EmptyPromptContextProvider()
            )
        )
        self._reasoning = reasoning if reasoning is not None else StaticReasoningClientProvider()
        self._run_factory = run_factory if run_factory is not None else SimpleRunRecordFactory()
        # None here defers to a per-message default in dispatch(): DefaultTurnAccounting
        # needs the message, so it cannot be resolved once at construction.
        self._accounting = accounting
        self._error_reporter = error_reporter if error_reporter is not None else NoopErrorReporter()
        self._gather_enabled = gather_enabled
        self._confirm_fn = confirm_fn
        self._is_tty = is_tty
        self._tool_hooks = tool_hooks

    def _accounting_for(self, message: str) -> TurnAccounting:
        if self._accounting is not None:
            return self._accounting
        if hasattr(self._store, "storage"):
            return DefaultTurnAccounting(self._store, message)
        return NoopTurnAccounting()

    def dispatch(self, message: str) -> ShellTurnResult:
        """Run one full turn for ``message`` and return the :class:`ShellTurnResult`."""
        accounting = self._accounting_for(message)

        def execute_actions(
            text: str,
            *,
            confirm_fn: ConfirmFn | None = None,
            is_tty: bool | None = None,
            turn_plan: TurnPlan | None = None,
        ) -> ToolCallingTurnResult:
            return run_action_agent_turn(
                text,
                self._store,
                output=self._output,
                tools=self._tools,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                turn_plan=turn_plan,
                error_reporter=self._error_reporter,
                tool_hooks=self._tool_hooks,
            )

        def answer(text: str, **kwargs: object) -> object:
            return stream_answer(
                text,
                self._store,
                self._output,
                prompts=self._prompts,
                reasoning=self._reasoning,
                run_factory=self._run_factory,
                error_reporter=self._error_reporter,
                **kwargs,  # type: ignore[arg-type]
            )

        def gather(
            text: str,
            *,
            is_tty: bool | None = None,
            turn_plan: TurnPlan | None = None,
        ) -> str | None:
            if not self._gather_enabled:
                return None
            resolved = turn_plan.resolved_integrations if turn_plan is not None else None
            return gather_tool_evidence(
                text,
                self._store,
                error_reporter=self._error_reporter,
                is_tty=is_tty,
                resolved_integrations=resolved,
            )

        return run_turn(
            message,
            self._store,
            execute_actions=execute_actions,
            answer=answer,
            gather=gather,
            accounting=accounting,
            confirm_fn=self._confirm_fn,
            is_tty=self._is_tty,
        )


__all__ = [
    "BufferOutputSink",
    "EmptyPromptContextProvider",
    "HeadlessAgent",
    "InMemorySessionStore",
    "NoopErrorReporter",
    "NoopTurnAccounting",
    "NullToolProvider",
    "SimpleRunRecord",
    "SimpleRunRecordFactory",
    "StaticReasoningClientProvider",
]
