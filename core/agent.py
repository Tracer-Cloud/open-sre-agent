"""Stateful ReAct agent — the shared primitive for all tool-calling surfaces."""

from __future__ import annotations

import importlib
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.agent_mixins import AgentEventEmitter, AgentToolFilter
from core.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
)
from core.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ProviderRequestEndEvent,
    ProviderRequestStartEvent,
    RuntimeEventCallback,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TupleEventCallback,
    TurnEndEvent,
    TurnStartEvent,
)
from core.execution import (
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
    public_tool_input,
)
from core.llm import agent_llm_client
from core.llm.types import ToolCall
from core.messages import (
    MessageFormatter,
    RuntimeMessage,
    RuntimeMessageLike,
    UserRuntimeMessage,
)
from core.provider import ProviderHooks, ProviderRequest
from core.types import RuntimeTool
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

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


@dataclass
class AgentRunResult:
    """Outcome of :meth:`Agent.run`.

    ``messages`` is the full conversation, ``final_text`` is the assistant's
    last no-tool-call turn, ``executed`` is the historical ordered list of raw
    tool payloads, and ``tool_results`` contains the structured runtime results.
    """

    messages: list[RuntimeMessage]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    tool_results: list[tuple[ToolCall, ToolExecutionResult]] = field(default_factory=list)
    terminated_by_tool: bool = False
    hit_iteration_cap: bool = False
    final_system_prompt: str = ""
    """System prompt sent to the LLM on the last request (post-hook), for debugging."""


class Agent[RuntimeToolT: RuntimeTool](AgentEventEmitter, AgentToolFilter):
    """Stateful, configurable ReAct agent.

    Owns the think → call-tools → observe loop and exposes hook methods so
    subclasses can customise stopping logic and tool filtering without
    re-implementing the loop.
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
        self._provider_hooks = provider_hooks or ProviderHooks()
        self._steering_messages: deque[str] = deque()
        self._follow_up_messages: deque[str] = deque()

    def steer(self, message: str) -> None:
        """Inject a user message into the active run before the next LLM turn."""
        if message.strip():
            self._steering_messages.append(message)

    def follow_up(self, message: str) -> None:
        """Queue a user message to run after the current turn would otherwise stop."""
        if message.strip():
            self._follow_up_messages.append(message)

    def run(
        self,
        initial_messages: Sequence[RuntimeMessageLike] | None = None,
        *,
        agent_context: AgentRuntimeRequest | None = None,
    ) -> AgentRunResult:
        """Run the think → call-tools → observe loop and return its outcome."""
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
        llm = self._llm
        msg_formatter = MessageFormatter(llm)
        runtime_tools = list(self._filter_tools(tools))
        tool_schemas = llm.tool_schemas(runtime_tools)
        ceiling = context_budget_ceiling_for_model(getattr(llm, "_model", None))
        executed: list[tuple[ToolCall, Any]] = []
        tool_results: list[tuple[ToolCall, ToolExecutionResult]] = []
        final_text = ""
        final_system_prompt = system
        hit_cap = True
        terminated_by_tool = False
        self._emit_runtime(
            AgentStartEvent(
                data={
                    "tool_count": len(runtime_tools),
                    "max_iterations": max_iterations,
                    "message_count": len(messages),
                }
            )
        )

        for iteration in range(max_iterations):
            self._drain_steering_messages(messages)
            self._emit_runtime(
                TurnStartEvent(
                    iteration=iteration,
                    data={"message_count": len(messages), "tool_count": len(runtime_tools)},
                )
            )
            transformed_messages = self._transform_context(messages)
            llm_messages = self._convert_to_llm(transformed_messages)
            enforce_context_budget(llm_messages, system=system, tools=tool_schemas, ceiling=ceiling)
            provider_request = ProviderRequest(
                messages=llm_messages,
                system=system,
                tools=tool_schemas,
                metadata={"iteration": iteration},
            )
            provider_request = self._before_provider_request(provider_request)
            final_system_prompt = provider_request.system
            self._emit_runtime(
                ProviderRequestStartEvent(
                    iteration=iteration,
                    message_count=len(provider_request.messages),
                )
            )
            response = llm.invoke(
                provider_request.messages,
                system=provider_request.system,
                tools=provider_request.tools,
            )
            response = self._after_provider_response(provider_request, response)
            self._emit_runtime(
                ProviderRequestEndEvent(
                    iteration=iteration,
                    has_tool_calls=response.has_tool_calls,
                )
            )
            assistant_message = msg_formatter.to_assistant_runtime_message(response)
            self._emit_runtime(MessageStartEvent(message=assistant_message, iteration=iteration))
            if response.content:
                self._emit_runtime(
                    MessageUpdateEvent(
                        message=assistant_message,
                        delta=response.content,
                        iteration=iteration,
                    )
                )
            messages.append(assistant_message)

            if not response.has_tool_calls:
                accept, nudge = self._should_accept_conclusion(
                    evidence_count=len(executed), iteration=iteration
                )
                if accept:
                    follow_up = self._pop_follow_up_message()
                    if follow_up is not None:
                        messages.append(UserRuntimeMessage(content=follow_up))
                        self._emit_runtime(
                            TurnEndEvent(
                                iteration=iteration,
                                message=assistant_message,
                                data={"accepted": False, "queued_follow_up": True},
                            )
                        )
                        continue
                    final_text = response.content or ""
                    hit_cap = False
                    self._emit_runtime(
                        TurnEndEvent(
                            iteration=iteration,
                            message=assistant_message,
                            data={"accepted": True},
                        )
                    )
                    break
                if nudge is None:
                    raise ValueError(
                        f"{type(self).__name__}._should_accept_conclusion returned "
                        "(False, None) — a nudge string is required when rejecting "
                        "the conclusion, otherwise the LLM will loop on an unchanged "
                        "message history until max_iterations."
                    )
                messages.append(UserRuntimeMessage(content=nudge))
                self._emit_runtime(
                    TurnEndEvent(
                        iteration=iteration,
                        message=assistant_message,
                        data={"accepted": False, "nudge": True},
                    )
                )
                continue

            for tc in response.tool_calls:
                self._emit_runtime(
                    ToolExecutionStartEvent(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        args=public_tool_input(tc.input),
                        iteration=iteration,
                    )
                )

            def on_tool_update(
                request: ToolExecutionRequest,
                update: Any,
                *,
                event_iteration: int = iteration,
            ) -> None:
                self._emit_tool_update(request, update, event_iteration=event_iteration)

            hooks = ToolExecutionHooks(
                before_tool_call=self._tool_hooks.before_tool_call,
                after_tool_call=self._tool_hooks.after_tool_call,
                on_tool_update=on_tool_update,
            )
            results = execute_tool_calls(
                response.tool_calls,
                runtime_tools,
                resolved,
                hooks=hooks,
                tool_resources=tool_resources,
            )
            provider_results = [result.provider_content() for result in results]
            tool_result_message = msg_formatter.to_tool_result_runtime_message(
                response.tool_calls, provider_results
            )
            messages.append(tool_result_message)

            for tc, result in zip(response.tool_calls, results):
                compat_payload = result.compat_payload()
                executed.append((tc, compat_payload))
                tool_results.append((tc, result))
                self._emit_runtime(
                    ToolExecutionEndEvent(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        args=public_tool_input(tc.input),
                        result=redact_sensitive(compat_payload),
                        is_error=result.is_error,
                        iteration=iteration,
                        data={"terminate": result.terminate},
                    )
                )
            self._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    tool_results=tuple(result.compat_payload() for result in results),
                    data={"accepted": False},
                )
            )
            if any(result.terminate for result in results):
                terminated_by_tool = True
                hit_cap = False
                break

        run_result = AgentRunResult(
            messages=messages,
            final_text=final_text,
            executed=executed,
            tool_results=tool_results,
            terminated_by_tool=terminated_by_tool,
            hit_iteration_cap=hit_cap,
            final_system_prompt=final_system_prompt,
        )
        self._emit_runtime(
            AgentEndEvent(
                messages=tuple(messages),
                data={
                    "final_text": final_text,
                    "hit_iteration_cap": hit_cap,
                    "terminated_by_tool": terminated_by_tool,
                    "message_count": len(messages),
                    "executed_count": len(executed),
                },
            )
        )
        return run_result

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

    def _drain_steering_messages(self, messages: list[RuntimeMessage]) -> None:
        while self._steering_messages:
            messages.append(UserRuntimeMessage(content=self._steering_messages.popleft()))

    def _pop_follow_up_message(self) -> str | None:
        if not self._follow_up_messages:
            return None
        return self._follow_up_messages.popleft()

    def _emit_tool_update(
        self,
        request: ToolExecutionRequest,
        update: Any,
        *,
        event_iteration: int,
    ) -> None:
        if self._tool_hooks.on_tool_update is not None:
            try:
                self._tool_hooks.on_tool_update(request, update)
            except Exception:  # noqa: BLE001 - observer failures must not break execution
                logger.debug(
                    "[runtime] on_tool_update(%s) raised; ignoring",
                    request.tool_call.name,
                    exc_info=True,
                )
        self._emit_runtime(
            ToolExecutionUpdateEvent(
                tool_call_id=request.tool_call.id,
                tool_name=request.tool_call.name,
                args=public_tool_input(request.tool_call.input),
                partial_result=redact_sensitive(update),
                iteration=event_iteration,
            )
        )

    def _before_provider_request(self, request: ProviderRequest) -> ProviderRequest:
        try:
            return self._provider_hooks.apply_before_request(request)
        except Exception:  # noqa: BLE001 - provider hooks are observability/customization only
            logger.debug("[runtime] before_provider_request raised; ignoring", exc_info=True)
            return request

    def _after_provider_response(self, request: ProviderRequest, response: Any) -> Any:
        try:
            return self._provider_hooks.apply_after_response(request, response)
        except Exception:  # noqa: BLE001 - preserve the transcript if hooks fail
            logger.debug("[runtime] after_provider_response raised; ignoring", exc_info=True)
            return response

    def _transform_context(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        try:
            return self._provider_hooks.apply_transform_context(messages)
        except Exception:  # noqa: BLE001 - fall back to the unmodified transcript
            logger.debug(
                "[runtime] transform_context raised; using original messages", exc_info=True
            )
            return list(messages)

    def _convert_to_llm(self, messages: list[RuntimeMessage]) -> list[dict[str, Any]]:
        # ``run()`` resolves ``self._llm`` before entering the loop; this method
        # is a per-iteration helper and never called before then.
        assert self._llm is not None, (
            "_convert_to_llm called before run() resolved self._llm — "
            "callers must go through run(), not private helpers"
        )
        llm = self._llm
        try:
            return self._provider_hooks.apply_convert_to_llm(llm, messages)
        except Exception:  # noqa: BLE001 - fall back to the standard provider conversion
            logger.debug("[runtime] convert_to_llm raised; using default conversion", exc_info=True)
            return MessageFormatter(llm).to_provider_messages(messages)
