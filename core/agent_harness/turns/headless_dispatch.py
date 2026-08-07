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

from typing import Any

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import (
    AnswerRequest,
    ConfirmFn,
    ConsoleBindable,
    ErrorReporter,
    OutputBindable,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    SessionBindable,
    SessionStore,
    ToolProvider,
    TurnAccounting,
)
from core.agent_harness.prompts.grounding import (
    DefaultPromptContextProvider,
    supports_default_prompt_context,
)
from core.agent_harness.turns.action_driver import (
    ActionTurnRunner,
)
from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.evidence_driver import gather_tool_evidence
from core.agent_harness.turns.gather_ports import GATHER_DISABLED, GatherPorts
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
from core.agent_harness.turns.orchestrator import stream_answer
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.execution import ToolExecutionHooks


class _Unmentioned:
    """Marks a port the caller did not mention, as distinct from one cleared.

    ``bind_turn(output=...)`` must leave the hooks alone, but
    ``bind_turn(tool_hooks=None)`` must clear them — the gateway passes ``None``
    for a sink that has no approval hooks, and a pooled agent would otherwise
    keep the previous sink's callback and send approvals to the wrong
    conversation.
    """


_UNMENTIONED = _Unmentioned()


class HeadlessAgent:
    """Runs full agent turns headlessly from a fixed set of configured ports.

    Construct once with the ports/dependencies, then call :meth:`dispatch` per
    message. ``tools`` is required — a surface that genuinely wants a text-only
    turn passes :class:`NullToolProvider` explicitly. Every other port defaults
    to an in-memory headless adapter. ``reasoning`` defaults to "no client" (the
    conversational assistant is skipped) so a turn runs with zero configuration;
    inject a client to get an actual answer.

    ``gather`` is a :class:`~core.agent_harness.turns.gather_ports.GatherPorts`
    describing the evidence pass: whether it runs, its loop budget, and the
    hooks a surface plugs in to stream progress and persist tool calls. It
    defaults to ``GATHER_DISABLED`` because the pass reaches out to live
    integrations — a caller opts in with ``GatherPorts()``.
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
        gather: GatherPorts | None = None,
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
        self._gather_ports = gather if gather is not None else GATHER_DISABLED
        self._confirm_fn = confirm_fn
        self._is_tty = is_tty
        self._tool_hooks = tool_hooks
        self._action_runner = self._new_action_runner()

    def _new_action_runner(self) -> ActionTurnRunner:
        return ActionTurnRunner(
            output=self._output,
            tools=self._tools,
            error_reporter=self._error_reporter,
            tool_hooks=self._tool_hooks,
        )

    def bind_session(self, session: SessionStore) -> None:
        """Retarget this agent at a freshly resolved session.

        Gateway ``SessionManager.resolve`` returns a new ``SessionCore`` each
        turn (same id, restored state). Cached agents must follow that object
        so tools/prompts see current integrations and chat metadata.

        Only ports that implement :class:`~core.agent_harness.ports.SessionBindable`
        are rebound — silent ``getattr`` skips are avoided so a missing binder
        on a session-aware default port is a type/test gap, not a runtime miss.
        """
        self._store = session
        for port in (self._tools, self._prompts, self._reasoning, self._run_factory):
            if isinstance(port, SessionBindable):
                port.bind_session(session)

    def bind_turn(
        self,
        *,
        output: OutputSink | None = None,
        accounting: TurnAccounting | None = None,
        tool_hooks: ToolExecutionHooks | None | _Unmentioned = _UNMENTIONED,
        session: SessionStore | None = None,
        console: Any | None = None,
    ) -> None:
        """Swap turn-scoped ports so one agent can serve many turns.

        Per-message accounting and (when provided) the current session object
        are rebound each inbound message. ``console`` rebinds a
        :class:`~core.agent_harness.ports.ConsoleBindable` tool provider so
        cooperative cancel (``cancel_requested``) is per-turn.

        Gateway keeps a stable ``LiveOutputSink`` on the pooled agent and
        rebinds the outer transport sink via ``LiveOutputSink.bind`` — it does
        not pass ``output=`` here. Hosts that replace the ``OutputSink`` object
        itself must pass ``output=`` so :class:`~core.agent_harness.ports.OutputBindable`
        ports (reasoning error rendering) follow the new sink.
        """
        if session is not None:
            self.bind_session(session)
        if console is not None and isinstance(self._tools, ConsoleBindable):
            self._tools.bind_console(console)
        runner_changed = False
        if output is not None:
            self._output = output
            if isinstance(self._reasoning, OutputBindable):
                self._reasoning.bind_output(output)
            runner_changed = True
        if accounting is not None:
            self._accounting = accounting
        if not isinstance(tool_hooks, _Unmentioned):
            self._tool_hooks = tool_hooks
            runner_changed = True
        if runner_changed:
            self._action_runner = self._new_action_runner()

    def _take_accounting(self, message: str) -> TurnAccounting:
        """Return turn accounting and clear the slot (consume-once).

        A prior turn's ``DefaultTurnAccounting`` (which captures that turn's
        prompt text) must not leak into the next ``dispatch`` when a host
        forgets ``bind_turn(accounting=…)``.
        """
        accounting = self._accounting
        self._accounting = None
        if accounting is not None:
            return accounting
        if hasattr(self._store, "storage"):
            return DefaultTurnAccounting(self._store, message)
        return NoopTurnAccounting()

    def _execute_actions(
        self,
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        return self._action_runner.run(
            text,
            self._store,
            turn_plan=turn_plan,
            is_tty=is_tty,
            confirm_fn=confirm_fn,
        )

    def _answer(self, text: str, request: AnswerRequest) -> object:
        return stream_answer(
            text,
            self._store,
            self._output,
            prompts=self._prompts,
            reasoning=self._reasoning,
            run_factory=self._run_factory,
            error_reporter=self._error_reporter,
            request=request,
        )

    def _gather(self, text: str, *, turn_plan: TurnPlan | None = None) -> str | None:
        if not self._gather_ports.enabled:
            return None
        from core.agent_harness.turns.host_cancel import host_cancel_requested

        if host_cancel_requested(self._output):
            return None
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return gather_tool_evidence(
            text,
            self._store,
            error_reporter=self._error_reporter,
            resolved_integrations=resolved,
            max_iterations=self._gather_ports.max_iterations,
            on_progress=self._gather_ports.on_progress,
            persist=self._gather_ports.persist,
            is_cancelled=lambda: host_cancel_requested(self._output),
        )

    def dispatch(self, message: str) -> TurnResult:
        """Run one full turn for ``message`` via the common chat host API."""
        return dispatch_chat_turn(
            message,
            self._store,
            ChatTurnBindings(
                execute_actions=self._execute_actions,
                answer=self._answer,
                gather=self._gather,
                accounting=self._take_accounting(message),
                confirm_fn=self._confirm_fn,
                is_tty=self._is_tty,
                surface=self._prompts.surface(),
                output=self._output,
            ),
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
