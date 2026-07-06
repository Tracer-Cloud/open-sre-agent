"""Headless programmatic entry point and in-memory port adapters.

This is the proof that the agent is decoupled from any terminal: a caller (an
HTTP handler, a script, a test) can run a full turn with only a message. All the
surface concerns are satisfied by the in-memory adapters below, but every
dependency is injectable so a real surface can override any of them.

Example::

    from core.agent_harness.turns.headless_dispatch import (
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

from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
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
from core.agent_harness.providers.default_prompt_context import (
    DefaultPromptContextProvider,
    supports_default_prompt_context,
)
from core.agent_harness.providers.default_providers import DefaultTurnAccounting
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
from core.execution import ToolExecutionHooks


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
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        return run_action_agent_turn(
            text,
            store,
            output=output,
            tools=tools,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            turn_plan=turn_plan,
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

    def gather(
        text: str,
        *,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> str | None:
        if not gather_enabled:
            return None
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return gather_tool_evidence(
            text,
            store,
            error_reporter=error_reporter,
            is_tty=is_tty,
            resolved_integrations=resolved,
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
