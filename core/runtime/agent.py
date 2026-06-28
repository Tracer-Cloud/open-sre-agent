"""Stateful ReAct agent — the shared primitive for all tool-calling surfaces."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from context.agent_context import AgentContext
from core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
)
from core.runtime.events import (
    AgentEndEvent,
    AgentStartEvent,
    LegacyLoopEventCallback,
    MessageStartEvent,
    MessageUpdateEvent,
    ProviderRequestEndEvent,
    ProviderRequestStartEvent,
    RuntimeEvent,
    RuntimeEventCallback,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    legacy_callback_payload,
    runtime_event_from_legacy,
)
from core.runtime.execution import (
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
    public_tool_input,
)
from core.runtime.llm.agent_llm_client import ToolCall
from core.runtime.messages import (
    RuntimeMessage,
    RuntimeMessageLike,
    convert_to_llm_messages,
    ensure_runtime_messages,
    runtime_assistant_message,
    runtime_tool_result_message,
    user_runtime_message,
)
from core.runtime.provider import ProviderHooks, ProviderRequest
from core.runtime.types import RuntimeTool
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

# Backward-compatible callback type: called with ``(event_kind, data_dict)``.
LoopEventCallback = LegacyLoopEventCallback


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


# Backward-compat alias — callers that still reference ToolLoopResult compile unchanged.
ToolLoopResult = AgentRunResult


class Agent[RuntimeToolT: RuntimeTool]:
    """Stateful, configurable ReAct agent.

    Owns the think → call-tools → observe loop and exposes hook methods so
    subclasses can customise stopping logic and tool filtering without
    re-implementing the loop.
    """

    def __init__(
        self,
        *,
        llm: Any,
        system: str,
        tools: Sequence[RuntimeToolT],
        resolved_integrations: dict[str, Any],
        max_iterations: int,
        on_event: LoopEventCallback | None = None,
        on_runtime_event: RuntimeEventCallback | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
        provider_hooks: ProviderHooks | None = None,
    ) -> None:
        self._llm = llm
        self._system = system
        self._tools = list(tools)
        self._resolved = resolved_integrations
        self._max_iterations = max_iterations
        self._on_legacy_event = on_event
        self._on_runtime_event = on_runtime_event
        self._tool_hooks = tool_hooks or ToolExecutionHooks()
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
        agent_context: AgentContext | None = None,
    ) -> AgentRunResult:
        """Run the think → call-tools → observe loop and return its outcome."""
        if agent_context is not None:
            agent_context.validate_runtime_request()
            messages = agent_context.runtime_messages()
            system = agent_context.system_prompt
            tools = list(agent_context.active_tools)
            resolved = agent_context.resolved_integrations
            max_iterations = agent_context.max_iterations
        elif initial_messages is not None:
            messages = ensure_runtime_messages(initial_messages)
            system = self._system
            tools = list(self._tools)
            resolved = self._resolved
            max_iterations = self._max_iterations
        else:
            raise ValueError("Agent.run requires initial_messages or agent_context.")

        runtime_tools = list(self._filter_tools(tools))
        tool_schemas = self._llm.tool_schemas(runtime_tools)
        ceiling = context_budget_ceiling_for_model(getattr(self._llm, "_model", None))
        executed: list[tuple[ToolCall, Any]] = []
        tool_results: list[tuple[ToolCall, ToolExecutionResult]] = []
        final_text = ""
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
            self._emit_runtime(
                ProviderRequestStartEvent(
                    iteration=iteration,
                    message_count=len(provider_request.messages),
                )
            )
            response = self._llm.invoke(
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
            assistant_message = runtime_assistant_message(self._llm, response)
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
                        messages.append(user_runtime_message(follow_up, queued_kind="follow_up"))
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
                messages.append(user_runtime_message(nudge))
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
            results = execute_tool_calls(response.tool_calls, runtime_tools, resolved, hooks=hooks)
            provider_results = [result.provider_content() for result in results]
            tool_result_message = runtime_tool_result_message(
                self._llm, response.tool_calls, provider_results
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

    def _filter_tools(self, tools: list[RuntimeToolT]) -> list[RuntimeToolT]:
        """Hook: narrow the tool list the agent will see."""
        return tools

    def _drain_steering_messages(self, messages: list[RuntimeMessage]) -> None:
        while self._steering_messages:
            messages.append(
                user_runtime_message(self._steering_messages.popleft(), queued_kind="steer")
            )

    def _pop_follow_up_message(self) -> str | None:
        if not self._follow_up_messages:
            return None
        return self._follow_up_messages.popleft()

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        event = runtime_event_from_legacy(kind, data)
        if event is not None:
            self._emit_runtime(event)
            return
        self._emit_legacy(kind, data)

    def _emit_runtime(self, event: RuntimeEvent) -> None:
        if self._on_runtime_event is not None:
            try:
                self._on_runtime_event(event)
            except Exception:  # noqa: BLE001 - event rendering must never break the loop
                logger.debug(
                    "[runtime] on_runtime_event(%s) raised; ignoring",
                    event.type,
                    exc_info=True,
                )
        legacy = legacy_callback_payload(event)
        if legacy is not None:
            self._emit_legacy(*legacy)

    def _emit_legacy(self, kind: str, data: dict[str, Any]) -> None:
        if self._on_legacy_event is not None:
            try:
                self._on_legacy_event(kind, data)
            except Exception:  # noqa: BLE001 - event rendering must never break the loop
                logger.debug("[runtime] on_event(%s) raised; ignoring", kind, exc_info=True)

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
        try:
            return self._provider_hooks.apply_convert_to_llm(self._llm, messages)
        except Exception:  # noqa: BLE001 - fall back to the standard provider conversion
            logger.debug("[runtime] convert_to_llm raised; using default conversion", exc_info=True)
            return convert_to_llm_messages(self._llm, messages)
