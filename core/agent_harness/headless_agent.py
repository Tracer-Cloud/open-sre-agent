"""Headless programmatic entry point for the agent subsystem.

This is the proof that the agent is decoupled from any terminal: a caller (an
HTTP handler, a script, a test) can run a full turn with only a message. All the
surface concerns are satisfied by the in-memory adapters in
:mod:`core.agent_harness.headless`, but every dependency is injectable so a real surface can
override any of them.

Example::

    from core.agent_harness.headless_agent import dispatch_message_to_headless_agent
    from core.agent_harness.headless import InMemorySessionStore, StaticReasoningClientProvider

    class _Echo:
        def invoke_stream(self, prompt):
            yield "hello"

    result = dispatch_message_to_headless_agent(
        "hi there",
        reasoning=StaticReasoningClientProvider(client=_Echo()),
    )
    print(result.assistant_response_text)  # -> "hello"
"""

from __future__ import annotations

from core.agent_harness.action_agent import run_agent_turn as run_action_agent_turn
from core.agent_harness.evidence_agent import gather_tool_evidence
from core.agent_harness.headless import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionStore,
    NoopErrorReporter,
    NoopTurnAccounting,
    NullToolProvider,
    SimpleRunRecordFactory,
    StaticReasoningClientProvider,
)
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
from core.agent_harness.turn_context import TurnContext
from core.agent_harness.turn_orchestrator import answer_cli_agent, run_turn
from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.execution import ToolExecutionHooks


def dispatch_message_to_headless_agent(
    message: str,
    *,
    session: SessionStore | None = None,
    output: OutputSink | None = None,
    tools: ToolProvider | None = None,
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

    Every port defaults to an in-memory headless adapter; pass concrete
    implementations to drive a real surface. ``reasoning`` defaults to "no
    client" (the conversational assistant is skipped) so a turn runs with zero
    configuration; inject a client to get an actual answer. ``gather_enabled``
    turns on the live evidence-gather pass (off by default, since it reaches out
    to integrations).
    """
    store: SessionStore = session if session is not None else InMemorySessionStore()
    output = output if output is not None else BufferOutputSink()
    tools = tools if tools is not None else NullToolProvider()
    prompts = prompts if prompts is not None else EmptyPromptContextProvider()
    reasoning = reasoning if reasoning is not None else StaticReasoningClientProvider()
    run_factory = run_factory if run_factory is not None else SimpleRunRecordFactory()
    accounting = accounting if accounting is not None else NoopTurnAccounting()
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
        return answer_cli_agent(
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


__all__ = ["dispatch_message_to_headless_agent"]
