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
from core.messages import ProviderMessage, RuntimeMessage, RuntimeMessageLike
from core.provider import ProviderHooks, ProviderRequest
from core.types import RuntimeTool

if TYPE_CHECKING:
    from core.agent_harness.models.turn_snapshot import AgentRuntimeRequest


class Agent[RuntimeToolT: RuntimeTool](EventEmitterMixin, ToolFilterMixin, SteeringMixin):
    """Stateful, configurable ReAct agent — the tool-calling agent shape.

    Wires per-run context into ``run_react_loop`` and exposes hook methods so
    subclasses can customise stopping logic and tool filtering without
    re-implementing the loop. For the direct-answer shape (no tools), see
    ``core/agent_harness/AGENTS.md``.
    """

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
        runtime_request: AgentRuntimeRequest | None = None,
    ) -> AgentRunResult:
        """Assemble the resolved per-run input and hand it to ``run_react_loop``."""
        run_input = self._build_run_input(initial_messages, runtime_request)
        return run_react_loop(run_input, self)

    def _build_run_input(
        self,
        initial_messages: Sequence[RuntimeMessageLike] | None,
        runtime_request: AgentRuntimeRequest | None,
    ) -> AgentRunInput[RuntimeToolT]:
        """Assemble the run input from whichever source the caller supplied.

        A ``runtime_request`` is validated and carries its own resolved context;
        raw ``initial_messages`` fall back to the construction-time config, which
        must include ``system`` and ``max_iterations``.
        """
        if runtime_request is not None:
            runtime_request.validate_runtime_request()
            return AgentRunInput[RuntimeToolT].from_runtime_request(
                runtime_request, llm=self._get_llm()
            )
        if initial_messages is not None:
            if self._system is None:
                raise ValueError("Agent.run: system= must be set at construction.")
            if self._max_iterations is None:
                raise ValueError("Agent.run: max_iterations= must be set at construction.")
            return AgentRunInput[RuntimeToolT].from_messages(
                initial_messages,
                llm=self._get_llm(),
                system=self._system,
                tools=self._tools,
                resolved=self._resolved,
                tool_resources=self._tool_resources,
                max_iterations=self._max_iterations,
            )
        raise ValueError("Agent.run requires initial_messages or runtime_request.")

    def _get_llm(self) -> Any:
        """Return the run's LLM: the instance given at construction, or the process-wide singleton."""
        if self._llm is None:
            self._llm = agent_llm_client.get_agent_llm()
        if self._llm is None:
            raise RuntimeError("Agent.run: llm must be set before the loop")
        return self._llm

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002
        iteration: int,  # noqa: ARG002
    ) -> tuple[bool, str | None]:
        """Hook: decide what to do when the LLM stops requesting tools.

        Return ``(True, None)`` to accept the conclusion and end the loop.
        Return ``(False, nudge_text)`` to inject a user message and continue.
        """
        return True, None

    # Thin forwarders to ``self._hooks`` (a ProviderHookDelegate). Kept as
    # methods rather than an exposed attribute so LoopHost's contract is
    # the four calls, not this concrete delegate type — see loop_host.py.
    def _transform_messages(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        return self._hooks.transform_messages(messages)

    def _convert_to_llm(self, llm: Any, messages: list[RuntimeMessage]) -> list[ProviderMessage]:
        return self._hooks.convert_to_llm(llm, messages)

    def _before_request(self, request: ProviderRequest) -> ProviderRequest:
        return self._hooks.before_request(request)

    def _after_response(self, request: ProviderRequest, response: Any) -> Any:
        return self._hooks.after_response(request, response)
