"""The ReAct loop algorithm: think -> call tools -> observe.

``run_react_loop`` is the algorithm ``core.agent.Agent`` wires context into. It
has no direct coupling to ``Agent`` — it only needs an ``AgentRunContext``
(the resolved, per-run inputs) and an ``AgentLoopHost`` (the narrow set of
hooks the loop calls back into: event emission, tool filtering, steering,
conclusion acceptance, and provider hooks). Any object implementing
``AgentLoopHost`` can drive this loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.context_budget import context_budget_ceiling_for_model, enforce_context_budget
from core.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ProviderRequestEndEvent,
    ProviderRequestStartEvent,
    RuntimeEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
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
from core.llm.types import ToolCall
from core.messages import MessageFormatter, ProviderMessage, RuntimeMessage, UserRuntimeMessage
from core.provider import ProviderRequest
from core.types import RuntimeTool
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """Outcome of :func:`run_react_loop` (returned as-is by ``Agent.run``).

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


@dataclass
class AgentRunContext[RuntimeToolT: RuntimeTool]:
    """Resolved, per-run inputs the loop needs — assembled once by ``Agent.run``."""

    llm: Any
    system: str
    tools: list[RuntimeToolT]
    resolved: dict[str, Any]
    tool_resources: dict[str, Any]
    max_iterations: int
    messages: list[RuntimeMessage]


class AgentLoopHost[RuntimeToolT: RuntimeTool](Protocol):
    """The narrow set of hooks ``run_react_loop`` calls back into.

    ``core.agent.Agent`` implements this via ``AgentEventEmitter``,
    ``AgentToolFilter``, ``AgentSteering`` (``core.agent_mixins``), and its own
    ``_should_accept_conclusion`` override hook plus thin ``AgentProviderHookDelegate``
    forwarders (``_transform_context``/``_convert_to_llm``/``_before_request``/
    ``_after_response``). The provider-hook delegate's concrete type is
    deliberately *not* part of this contract — only the method calls are —
    so a host can wire the four seams however it likes.
    """

    _tool_hooks: ToolExecutionHooks

    def _filter_tools(self, tools: list[RuntimeToolT]) -> list[RuntimeToolT]:
        pass

    def _emit_runtime(self, event: RuntimeEvent) -> None:
        pass

    def _drain_steering_messages(self, messages: list[RuntimeMessage]) -> None:
        pass

    def _pop_follow_up_message(self) -> str | None:
        pass

    def _should_accept_conclusion(
        self, *, evidence_count: int, iteration: int
    ) -> tuple[bool, str | None]:
        pass

    def _transform_context(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        pass

    def _convert_to_llm(self, llm: Any, messages: list[RuntimeMessage]) -> list[ProviderMessage]:
        pass

    def _before_request(self, request: ProviderRequest) -> ProviderRequest:
        pass

    def _after_response(self, request: ProviderRequest, response: Any) -> Any:
        pass


def _emit_tool_update(
    host: AgentLoopHost[Any],
    request: ToolExecutionRequest,
    update: Any,
    *,
    event_iteration: int,
) -> None:
    if host._tool_hooks.on_tool_update is not None:
        try:
            host._tool_hooks.on_tool_update(request, update)
        except Exception:  # noqa: BLE001 - observer failures must not break execution
            logger.debug(
                "[runtime] on_tool_update(%s) raised; ignoring",
                request.tool_call.name,
                exc_info=True,
            )
    host._emit_runtime(
        ToolExecutionUpdateEvent(
            tool_call_id=request.tool_call.id,
            tool_name=request.tool_call.name,
            args=public_tool_input(request.tool_call.input),
            partial_result=redact_sensitive(update),
            iteration=event_iteration,
        )
    )


def run_react_loop[RuntimeToolT: RuntimeTool](
    context: AgentRunContext[RuntimeToolT],
    host: AgentLoopHost[RuntimeToolT],
) -> AgentRunResult:
    """Run the think -> call-tools -> observe loop and return its outcome."""
    llm = context.llm
    system = context.system
    resolved = context.resolved
    tool_resources = context.tool_resources
    max_iterations = context.max_iterations
    messages = context.messages

    msg_formatter = MessageFormatter(llm)
    runtime_tools = list(host._filter_tools(context.tools))
    tool_schemas = llm.tool_schemas(runtime_tools)
    ceiling = context_budget_ceiling_for_model(getattr(llm, "_model", None))
    executed: list[tuple[ToolCall, Any]] = []
    tool_results: list[tuple[ToolCall, ToolExecutionResult]] = []
    final_text = ""
    final_system_prompt = system
    hit_cap = True
    terminated_by_tool = False
    host._emit_runtime(
        AgentStartEvent(
            data={
                "tool_count": len(runtime_tools),
                "max_iterations": max_iterations,
                "message_count": len(messages),
            }
        )
    )

    for iteration in range(max_iterations):
        host._drain_steering_messages(messages)
        host._emit_runtime(
            TurnStartEvent(
                iteration=iteration,
                data={"message_count": len(messages), "tool_count": len(runtime_tools)},
            )
        )
        transformed_messages = host._transform_context(messages)
        llm_messages = host._convert_to_llm(llm, transformed_messages)
        enforce_context_budget(llm_messages, system=system, tools=tool_schemas, ceiling=ceiling)
        provider_request = ProviderRequest(
            messages=llm_messages,
            system=system,
            tools=tool_schemas,
            metadata={"iteration": iteration},
        )
        provider_request = host._before_request(provider_request)
        final_system_prompt = provider_request.system or system
        host._emit_runtime(
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
        response = host._after_response(provider_request, response)
        host._emit_runtime(
            ProviderRequestEndEvent(
                iteration=iteration,
                has_tool_calls=response.has_tool_calls,
            )
        )
        assistant_message = msg_formatter.to_assistant_runtime_message(response)
        host._emit_runtime(MessageStartEvent(message=assistant_message, iteration=iteration))
        if response.content:
            host._emit_runtime(
                MessageUpdateEvent(
                    message=assistant_message,
                    delta=response.content,
                    iteration=iteration,
                )
            )
        messages.append(assistant_message)

        if not response.has_tool_calls:
            accept, nudge = host._should_accept_conclusion(
                evidence_count=len(executed), iteration=iteration
            )
            if accept:
                follow_up = host._pop_follow_up_message()
                if follow_up is not None:
                    messages.append(UserRuntimeMessage(content=follow_up))
                    host._emit_runtime(
                        TurnEndEvent(
                            iteration=iteration,
                            message=assistant_message,
                            data={"accepted": False, "queued_follow_up": True},
                        )
                    )
                    continue
                final_text = response.content or ""
                hit_cap = False
                host._emit_runtime(
                    TurnEndEvent(
                        iteration=iteration,
                        message=assistant_message,
                        data={"accepted": True},
                    )
                )
                break
            if nudge is None:
                raise ValueError(
                    f"{type(host).__name__}._should_accept_conclusion returned "
                    "(False, None) — a nudge string is required when rejecting "
                    "the conclusion, otherwise the LLM will loop on an unchanged "
                    "message history until max_iterations."
                )
            messages.append(UserRuntimeMessage(content=nudge))
            host._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    data={"accepted": False, "nudge": True},
                )
            )
            continue

        for tc in response.tool_calls:
            host._emit_runtime(
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
            _emit_tool_update(host, request, update, event_iteration=event_iteration)

        hooks = ToolExecutionHooks(
            before_tool_call=host._tool_hooks.before_tool_call,
            after_tool_call=host._tool_hooks.after_tool_call,
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
            host._emit_runtime(
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
        host._emit_runtime(
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
    host._emit_runtime(
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


__all__ = ["AgentLoopHost", "AgentRunContext", "AgentRunResult", "run_react_loop"]
