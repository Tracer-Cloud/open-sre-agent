"""Headless programmatic entry point and in-memory port adapters.

This is the proof that the agent is decoupled from any terminal: a caller (an
HTTP handler, a script, a test) can run a full turn with only a message. All the
surface concerns are satisfied by the in-memory adapters below, but every
dependency is injectable so a real surface can override any of them.

Example::

    from core.agent_harness.turns.headless_dispatch import (
        HeadlessAgent,
        InMemorySessionState,
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

from collections.abc import Callable
from dataclasses import replace

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import (
    AnswerRequest,
    ConfirmFn,
    ConsoleBindable,
    ErrorReporter,
    EvidenceGatherer,
    ExecuteActions,
    LlmFactory,
    OutputBindable,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    SessionBindable,
    SessionState,
    StreamAnswerFn,
    ToolProvider,
    TurnAccounting,
    TurnBinding,
)
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.evidence_driver import gather_tool_evidence
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.gather_ports import GatherPorts
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionState,
    NoopErrorReporter,
    NoopTurnAccounting,
    NullToolProvider,
    SimpleRunRecord,
    SimpleRunRecordFactory,
    StaticReasoningClientProvider,
)
from core.agent_harness.turns.host_cancel import host_cancel_requested
from core.agent_harness.turns.orchestrator import stream_answer
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.execution import ToolExecutionHooks


class HeadlessAgent:
    """Runs agent turns from a fixed set of ports; built by a port family.

    Every port is required here — the two families supply them:
    :class:`~core.agent_harness.turns.port_families.HeadlessPorts` (in-memory;
    scripts and tests) and :class:`~core.agent_harness.turns.port_families.DefaultPorts`
    (the product defaults; gateway and shell). Hosts do not call this
    constructor.

    Per message a host calls :meth:`handle` with a :class:`TurnBinding`; it
    binds the turn, dispatches, and continues while a session goal is
    attached. :meth:`dispatch` is the single-turn verb underneath; whole-stage
    replacements for tests go through :meth:`bind_stages`.
    """

    def __init__(
        self,
        *,
        tools: ToolProvider,
        session: SessionState,
        output: OutputSink,
        prompts: PromptContextProvider,
        reasoning: ReasoningClientProvider,
        run_factory: RunRecordFactory,
        error_reporter: ErrorReporter,
        gather: GatherPorts,
        llm_factory: LlmFactory | None = None,
    ) -> None:
        self._tools = tools
        self._llm_factory = llm_factory
        self._session: SessionState = session
        self._output: OutputSink = output
        self._prompts: PromptContextProvider = prompts
        self._reasoning = reasoning
        self._run_factory = run_factory
        self._error_reporter = error_reporter
        self._gather_ports = gather
        # Turn-scoped state; see bind_turn / bind_stages.
        self._accounting: TurnAccounting | None = None
        self._confirm_fn: ConfirmFn | None = None
        self._is_tty: bool | None = None
        self._tool_hooks: ToolExecutionHooks | None = None
        self._execute_actions_override: ExecuteActions | None = None
        self._answer_override: StreamAnswerFn | None = None
        self._gather_override: EvidenceGatherer | None = None
        self._action_runner = self._new_action_runner()

    def _new_action_runner(self) -> ActionTurnRunner:
        return ActionTurnRunner(
            output=self._output,
            tools=self._tools,
            llm_factory=self._llm_factory,
            error_reporter=self._error_reporter,
            tool_hooks=self._tool_hooks,
        )

    def _ports(self) -> tuple[object, ...]:
        """Every port this agent holds; rebinding asks each for the capability it needs."""
        return (
            self._tools,
            self._prompts,
            self._reasoning,
            self._run_factory,
            self._error_reporter,
            self._gather_ports.on_progress,
            self._gather_ports.persist,
        )

    def bind_session(self, session: SessionState) -> None:
        """Retarget this agent at a freshly resolved session.

        Gateway ``SessionManager.resolve`` returns a new ``SessionCore`` each
        turn (same id, restored state). Cached agents must follow that object
        so tools/prompts see current integrations and chat metadata.

        Every port that implements :class:`~core.agent_harness.ports.SessionBindable`
        is rebound; a session-aware port that lacks the protocol is a type/test
        gap, not a runtime miss.
        """
        self._session = session
        for port in self._ports():
            if isinstance(port, SessionBindable):
                port.bind_session(session)

    def bind_turn(self, binding: TurnBinding) -> None:
        """Bind the turn's ports and values; the whole binding replaces the previous one.

        Rebinding ``session`` retargets every :class:`SessionBindable` port,
        ``console`` every :class:`ConsoleBindable` port, and a new ``output``
        object every :class:`OutputBindable` port; a new output or a change of
        ``tool_hooks`` also rebuilds the action runner
        — an unchanged one keeps it. Gateway keeps a stable ``LiveOutputSink``
        and rebinds the transport sink inside it, so it leaves ``output`` unset.
        """
        if binding.session is not None:
            self.bind_session(binding.session)
        if binding.console is not None:
            for port in self._ports():
                if isinstance(port, ConsoleBindable):
                    port.bind_console(binding.console)
        runner_changed = False
        if binding.output is not None and binding.output is not self._output:
            self._output = binding.output
            for port in self._ports():
                if isinstance(port, OutputBindable):
                    port.bind_output(binding.output)
            runner_changed = True
        if binding.tool_hooks is not self._tool_hooks:
            self._tool_hooks = binding.tool_hooks
            runner_changed = True
        self._accounting = binding.accounting
        self._confirm_fn = binding.confirm_fn
        self._is_tty = binding.is_tty
        if runner_changed:
            self._action_runner = self._new_action_runner()

    def _take_accounting(self, message: str) -> TurnAccounting:
        """Return turn accounting and clear the slot (consume-once).

        A prior turn's ``DefaultTurnAccounting`` (which captures that turn's
        prompt text) must not leak into the next ``dispatch`` when a host
        binds a turn without accounting.
        """
        accounting = self._accounting
        self._accounting = None
        if accounting is not None:
            return accounting
        if hasattr(self._session, "store"):
            return DefaultTurnAccounting(self._session, message)
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
            self._session,
            turn_plan=turn_plan,
            is_tty=is_tty,
            confirm_fn=confirm_fn,
        )

    def _answer(self, text: str, request: AnswerRequest) -> object:
        return stream_answer(
            text,
            self._session,
            self._output,
            prompts=self._prompts,
            reasoning=self._reasoning,
            run_factory=self._run_factory,
            error_reporter=self._error_reporter,
            request=request,
        )

    def _gather(
        self, text: str, *, turn_plan: TurnPlan | None = None
    ) -> str | GatheredEvidence | None:
        if not self._gather_ports.enabled:
            return None
        if host_cancel_requested(self._output):
            return None
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return gather_tool_evidence(
            text,
            self._session,
            error_reporter=self._error_reporter,
            resolved_integrations=resolved,
            max_iterations=self._gather_ports.max_iterations,
            on_progress=self._gather_ports.on_progress,
            persist=self._gather_ports.persist,
            is_cancelled=lambda: host_cancel_requested(self._output),
        )

    def bind_stages(
        self,
        *,
        execute_actions: ExecuteActions | None = None,
        answer: StreamAnswerFn | None = None,
        gather_evidence: EvidenceGatherer | None = None,
    ) -> None:
        """Replace whole stages for the next dispatches; ``None`` restores the port-driven default.

        The per-turn counterpart of the constructor's stage overrides, for a
        long-lived agent that hosts many turns with different injected seams.
        """
        self._execute_actions_override = execute_actions
        self._answer_override = answer
        self._gather_override = gather_evidence

    def handle(
        self,
        text: str,
        binding: TurnBinding,
        *,
        accounting_factory: Callable[[str], TurnAccounting] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[SessionGoal], None] | None = None,
    ) -> TurnResult:
        """Handle one inbound message: bind the turn, dispatch, continue while a session goal is attached.

        The one host loop — gateway and shell call this and nothing else per
        message. ``binding`` states the whole turn; ``accounting_factory``
        (message → accounting) is applied per outer turn because the goal loop
        may dispatch several, each with different text; ``None`` uses the
        default per-message accounting. ``cancel_requested`` is checked between
        outer turns; ``on_progress`` receives the goal after each.
        """

        def _one_turn(message: str) -> TurnResult:
            accounting = accounting_factory(message) if accounting_factory is not None else None
            self.bind_turn(replace(binding, accounting=accounting))
            return self.dispatch(message)

        # The goal loop reads and writes goal state on the session this turn
        # states, not on whatever the previous binding left behind.
        session = binding.session if binding.session is not None else self._session
        return run_until_session_goal(
            _one_turn,
            session,
            text,
            cancel_requested=cancel_requested,
            on_progress=on_progress,
        ).last_result

    def dispatch(self, message: str) -> TurnResult:
        """Run one turn for ``message`` on the currently bound turn (see :meth:`handle`)."""
        return dispatch_chat_turn(
            message,
            self._session,
            ChatTurnBindings(
                execute_actions=self._execute_actions_override or self._execute_actions,
                answer=self._answer_override or self._answer,
                gather=self._gather_override or self._gather,
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
    "InMemorySessionState",
    "NoopErrorReporter",
    "NoopTurnAccounting",
    "NullToolProvider",
    "SimpleRunRecord",
    "SimpleRunRecordFactory",
    "StaticReasoningClientProvider",
]
