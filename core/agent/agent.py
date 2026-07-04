"""The reusable tool-calling agent every surface runs (shell, gateway, investigation).

You create an ``Agent`` with its config (LLM, system prompt, tools, iteration
cap); ``run()`` gathers that config for one run and hands it to
``core.agent.react_loop.run_react_loop``, which runs the actual
think -> call-tools -> observe loop. ``Agent`` stays thin: it holds the config
and provides the callback methods (from the mixins) the loop calls back into —
it does not contain the loop itself.

The other agent shape — a direct answer with no tools — is not an ``Agent``;
see ``core/agent_harness/AGENTS.md``.
"""

from __future__ import annotations

import importlib
from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from core.agent.mixins import EventEmitterMixin, SteeringMixin, ToolFilterMixin
from core.agent.provider_hooks import ProviderHookDelegate
from core.agent.react_loop import run_react_loop
from core.agent.run_io import AgentRunInput, AgentRunResult
from core.events import RuntimeEventCallback, TupleEventCallback
from core.execution import ToolExecutionHooks
from core.llm import agent_llm_client
from core.messages import MessageFormatter, ProviderMessage, RuntimeMessage, RuntimeMessageLike
from core.provider import ProviderHooks, ProviderRequest
from core.types import RuntimeTool

if TYPE_CHECKING:
    from core.agent_harness.models.turn_context import AgentRuntimeRequest
    from core.agent_harness.models.turn_results import ShellTurnResult
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


class Agent[RuntimeToolT: RuntimeTool](EventEmitterMixin, ToolFilterMixin, SteeringMixin):
    """Stateful, configurable ReAct agent — the tool-calling agent shape.

    Wires per-run context into ``run_react_loop`` and exposes hook methods so
    subclasses can customise stopping logic and tool filtering without
    re-implementing the loop. For the direct-answer shape (no tools), see
    ``core/agent_harness/AGENTS.md``.
    """

    @staticmethod
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
        """Run a full headless turn through the shared agent harness.

        ``tools`` is required — surfaces must decide explicitly whether to
        expose any. Callers that genuinely want a text-only turn pass
        :class:`~core.agent_harness.agents.headless_agent.NullToolProvider`.
        """
        # Resolved dynamically so this module keeps the layering one-way
        # (agent_harness -> core): a static import of the harness here would form a
        # core.agent <-> agent_harness.agents cycle (CodeQL py/cyclic-import).
        headless = importlib.import_module("core.agent_harness.agents.headless_agent")
        result: ShellTurnResult = headless.dispatch_message_to_headless_agent(
            message,
            tools=tools,
            session=session,
            output=output,
            prompts=prompts,
            reasoning=reasoning,
            run_factory=run_factory,
            accounting=accounting,
            error_reporter=error_reporter,
            gather_enabled=gather_enabled,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            tool_hooks=tool_hooks,
        )
        return result

    @staticmethod
    def resolve_integrations(session: SessionStore) -> dict[str, Any]:
        """Resolve integration configs for ``session``, using the session cache."""
        # importlib keeps the core -> agent_harness reach dynamic (no static cycle).
        resolution = importlib.import_module("core.agent_harness.integrations.resolution")
        cache = importlib.import_module("core.agent_harness.session.integrations_cache")

        cached = session.resolved_integrations_cache
        if cached is not None and (
            cache.has_resolved_integrations(cached) or not cache.has_only_runtime_metadata(cached)
        ):
            return dict(cached)

        resolved = resolution.resolve_integrations()
        if resolved:
            session.resolved_integrations_cache = cache.merge_resolved_integrations(
                cached, resolved
            )
        return dict(session.resolved_integrations_cache or {})

    def __init__(
        self,
        *,
        llm: Any | None = None,
        system: str | None = None,
        tools: Sequence[RuntimeToolT] | None = None,
        resolved_integrations: dict[str, Any] | None = None,
        max_iterations: int | None = None,
        on_event: TupleEventCallback | None = None,
        on_runtime_event: RuntimeEventCallback | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
        tool_resources: dict[str, Any] | None = None,
        provider_hooks: ProviderHooks | None = None,
    ) -> None:
        self._llm = llm
        self._system = system
        self._tools: list[RuntimeToolT] | None = list(tools) if tools is not None else None
        self._resolved = resolved_integrations
        self._max_iterations = max_iterations
        self._on_tuple_event = on_event
        self._on_runtime_event = on_runtime_event
        self._tool_hooks = tool_hooks or ToolExecutionHooks()
        self._tool_resources = dict(tool_resources or {})
        self._hooks = ProviderHookDelegate(provider_hooks or ProviderHooks())
        self._steering_messages: deque[str] = deque()
        self._follow_up_messages: deque[str] = deque()

    def run(
        self,
        initial_messages: Sequence[RuntimeMessageLike] | None = None,
        *,
        agent_context: AgentRuntimeRequest | None = None,
    ) -> AgentRunResult:
        """Resolve per-run context and hand it to ``run_react_loop``."""
        if agent_context is not None:
            agent_context.validate_runtime_request()
            messages = agent_context.runtime_messages()
            render_system_prompt = getattr(agent_context, "render_system_prompt", None)
            if callable(render_system_prompt):
                system = render_system_prompt()
            else:
                system = str(agent_context.system_prompt)
            tools = list(agent_context.active_tools)
            resolved = agent_context.resolved_integrations
            tool_resources = dict(getattr(agent_context, "tool_resources", {}) or {})
            max_iterations = agent_context.max_iterations
            if self._llm is None:
                self._llm = agent_llm_client.get_agent_llm()
        elif initial_messages is not None:
            if self._system is None:
                raise ValueError("Agent.run: system= must be set at construction.")
            if self._max_iterations is None:
                raise ValueError("Agent.run: max_iterations= must be set at construction.")
            if self._llm is None:
                self._llm = agent_llm_client.get_agent_llm()
            system = self._system
            tools = list(self._tools) if self._tools is not None else []
            resolved = dict(self._resolved) if self._resolved is not None else {}
            max_iterations = self._max_iterations
            messages = MessageFormatter.normalize(initial_messages)
            tool_resources = dict(self._tool_resources)
        else:
            raise ValueError("Agent.run requires initial_messages or agent_context.")

        assert self._llm is not None, "Agent.run: llm must be set before the loop"
        run_context = AgentRunInput[RuntimeToolT](
            llm=self._llm,
            system=system,
            tools=tools,
            resolved=resolved,
            tool_resources=tool_resources,
            max_iterations=max_iterations,
            messages=messages,
        )
        return run_react_loop(run_context, self)

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 - used by overrides
        iteration: int,  # noqa: ARG002 - used by overrides
    ) -> tuple[bool, str | None]:
        """Hook: decide what to do when the LLM stops requesting tools.

        Return ``(True, None)`` to accept the conclusion and end the loop.
        Return ``(False, nudge_text)`` to inject a user message and continue.
        """
        return True, None

    # Thin forwarders to ``self._hooks`` (a ProviderHookDelegate). Kept as
    # methods rather than an exposed attribute so LoopHost's contract is
    # the four calls, not this concrete delegate type — see loop_host.py.
    def _transform_context(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        return self._hooks.transform_context(messages)

    def _convert_to_llm(self, llm: Any, messages: list[RuntimeMessage]) -> list[ProviderMessage]:
        return self._hooks.convert_to_llm(llm, messages)

    def _before_request(self, request: ProviderRequest) -> ProviderRequest:
        return self._hooks.before_request(request)

    def _after_response(self, request: ProviderRequest, response: Any) -> Any:
        return self._hooks.after_response(request, response)
