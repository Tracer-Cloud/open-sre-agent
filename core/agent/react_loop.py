"""The ReAct loop: reason, act (call tools), observe results, repeat.

``ReactLoop`` runs the loop. Each pass asks the LLM what to do (reason); if it
requests tools, they run and their results are fed back in (act + observe); this
repeats until the LLM answers with no tool calls or an iteration cap is hit. The
loop knows nothing about ``Agent`` — it takes an ``AgentRunInput``
(``core.agent.run_io``, the resolved inputs) and a ``LoopHost``
(``core.agent.loop_host``, the callbacks it needs), so anything implementing that
host can drive it. ``run_react_loop`` is the one-line functional entry.
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent.loop_host import LoopHost
from core.agent.run_io import AgentRunInput, AgentRunResult
from core.context_budget import context_budget_ceiling_for_model, enforce_context_budget
from core.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ProviderRequestEndEvent,
    ProviderRequestStartEvent,
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
from core.messages import MessageFormatter, UserRuntimeMessage
from core.provider import ProviderRequest
from core.types import RuntimeTool
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)


class ReactLoop[RuntimeToolT: RuntimeTool]:
    """Runs one ReAct loop over a single ``AgentRunInput``.

    The per-run state — the running message list, the tool results, whether a
    tool ended the turn — lives in the instance fields; ``run()`` drives it to
    completion. The loop never decides things like which tools to expose or when
    to stop; it asks the ``LoopHost`` at each of those points.
    """

    def __init__(
        self,
        run_input: AgentRunInput[RuntimeToolT],
        host: LoopHost[RuntimeToolT],
    ) -> None:
        self._host = host
        self._llm = run_input.llm
        self._system = run_input.system
        self._resolved = run_input.resolved
        self._tool_resources = run_input.tool_resources
        self._max_iterations = run_input.max_iterations
        self._messages = run_input.messages
        self._msg_formatter = MessageFormatter(self._llm)
        self._runtime_tools = list(host._filter_tools(run_input.tools))
        self._tool_schemas = self._llm.tool_schemas(self._runtime_tools)
        self._ceiling = context_budget_ceiling_for_model(getattr(self._llm, "_model", None))
        self._executed: list[tuple[ToolCall, Any]] = []
        self._tool_results: list[tuple[ToolCall, ToolExecutionResult]] = []
        self._final_text = ""
        self._final_system_prompt = self._system
        self._hit_cap = True
        self._terminated_by_tool = False

    def run(self) -> AgentRunResult:
        """Drive the loop to completion and return its outcome."""
        self._host._emit_runtime(
            AgentStartEvent(
                data={
                    "tool_count": len(self._runtime_tools),
                    "max_iterations": self._max_iterations,
                    "message_count": len(self._messages),
                }
            )
        )
        for iteration in range(self._max_iterations):
            if self._run_iteration(iteration):
                break
        return self._finalize()

    def _run_iteration(self, iteration: int) -> bool:
        """Run one think -> observe step. Return True when the loop should stop."""
        self._host._drain_steering_messages(self._messages)
        self._host._emit_runtime(
            TurnStartEvent(
                iteration=iteration,
                data={"message_count": len(self._messages), "tool_count": len(self._runtime_tools)},
            )
        )
        response = self._think(iteration)
        assistant_message = self._msg_formatter.to_assistant_runtime_message(response)
        self._host._emit_runtime(MessageStartEvent(message=assistant_message, iteration=iteration))
        if response.content:
            self._host._emit_runtime(
                MessageUpdateEvent(
                    message=assistant_message,
                    delta=response.content,
                    iteration=iteration,
                )
            )
        self._messages.append(assistant_message)

        if not response.has_tool_calls:
            return self._handle_conclusion(response, assistant_message, iteration)
        return self._observe(response, assistant_message, iteration)

    def _think(self, iteration: int) -> Any:
        """Build the request, apply the provider hooks, and call the LLM."""
        transformed_messages = self._host._transform_messages(self._messages)
        llm_messages = self._host._convert_to_llm(self._llm, transformed_messages)
        enforce_context_budget(
            llm_messages, system=self._system, tools=self._tool_schemas, ceiling=self._ceiling
        )
        provider_request = ProviderRequest(
            messages=llm_messages,
            system=self._system,
            tools=self._tool_schemas,
            metadata={"iteration": iteration},
        )
        provider_request = self._host._before_request(provider_request)
        self._final_system_prompt = provider_request.system or self._system
        self._host._emit_runtime(
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
        response = self._host._after_response(provider_request, response)
        self._host._emit_runtime(
            ProviderRequestEndEvent(
                iteration=iteration,
                has_tool_calls=response.has_tool_calls,
            )
        )
        return response

    def _handle_conclusion(self, response: Any, assistant_message: Any, iteration: int) -> bool:
        """No tool calls: accept the answer (maybe after a follow-up) or nudge and continue."""
        accept, nudge = self._host._should_accept_conclusion(
            evidence_count=len(self._executed), iteration=iteration
        )
        if accept:
            follow_up = self._host._pop_follow_up_message()
            if follow_up is not None:
                self._messages.append(UserRuntimeMessage(content=follow_up))
                self._host._emit_runtime(
                    TurnEndEvent(
                        iteration=iteration,
                        message=assistant_message,
                        data={"accepted": False, "queued_follow_up": True},
                    )
                )
                return False
            self._final_text = response.content or ""
            self._hit_cap = False
            self._host._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    data={"accepted": True},
                )
            )
            return True
        if nudge is None:
            raise ValueError(
                f"{type(self._host).__name__}._should_accept_conclusion returned "
                "(False, None) — a nudge string is required when rejecting "
                "the conclusion, otherwise the LLM will loop on an unchanged "
                "message history until max_iterations."
            )
        self._messages.append(UserRuntimeMessage(content=nudge))
        self._host._emit_runtime(
            TurnEndEvent(
                iteration=iteration,
                message=assistant_message,
                data={"accepted": False, "nudge": True},
            )
        )
        return False

    def _observe(self, response: Any, assistant_message: Any, iteration: int) -> bool:
        """Execute the requested tools, record results, emit events. Return True if a tool terminated."""
        for tc in response.tool_calls:
            self._host._emit_runtime(
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
            before_tool_call=self._host._tool_hooks.before_tool_call,
            after_tool_call=self._host._tool_hooks.after_tool_call,
            on_tool_update=on_tool_update,
        )
        results = execute_tool_calls(
            response.tool_calls,
            self._runtime_tools,
            self._resolved,
            hooks=hooks,
            tool_resources=self._tool_resources,
        )
        provider_results = [result.provider_content() for result in results]
        tool_result_message = self._msg_formatter.to_tool_result_runtime_message(
            response.tool_calls, provider_results
        )
        self._messages.append(tool_result_message)

        for tc, result in zip(response.tool_calls, results):
            compat_payload = result.compat_payload()
            self._executed.append((tc, compat_payload))
            self._tool_results.append((tc, result))
            self._host._emit_runtime(
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
        self._host._emit_runtime(
            TurnEndEvent(
                iteration=iteration,
                message=assistant_message,
                tool_results=tuple(result.compat_payload() for result in results),
                data={"accepted": False},
            )
        )
        if any(result.terminate for result in results):
            self._terminated_by_tool = True
            self._hit_cap = False
            return True
        return False

    def _emit_tool_update(
        self, request: ToolExecutionRequest, update: Any, *, event_iteration: int
    ) -> None:
        if self._host._tool_hooks.on_tool_update is not None:
            try:
                self._host._tool_hooks.on_tool_update(request, update)
            except Exception:  # noqa: BLE001 - observer failures must not break execution
                logger.debug(
                    "[runtime] on_tool_update(%s) raised; ignoring",
                    request.tool_call.name,
                    exc_info=True,
                )
        self._host._emit_runtime(
            ToolExecutionUpdateEvent(
                tool_call_id=request.tool_call.id,
                tool_name=request.tool_call.name,
                args=public_tool_input(request.tool_call.input),
                partial_result=redact_sensitive(update),
                iteration=event_iteration,
            )
        )

    def _finalize(self) -> AgentRunResult:
        """Build the run result, emit the end-of-run event, and return the result."""
        run_result = AgentRunResult(
            messages=self._messages,
            final_text=self._final_text,
            executed=self._executed,
            tool_results=self._tool_results,
            terminated_by_tool=self._terminated_by_tool,
            hit_iteration_cap=self._hit_cap,
            final_system_prompt=self._final_system_prompt,
        )
        self._host._emit_runtime(
            AgentEndEvent(
                messages=tuple(self._messages),
                data={
                    "final_text": self._final_text,
                    "hit_iteration_cap": self._hit_cap,
                    "terminated_by_tool": self._terminated_by_tool,
                    "message_count": len(self._messages),
                    "executed_count": len(self._executed),
                },
            )
        )
        return run_result


def run_react_loop[RuntimeToolT: RuntimeTool](
    run_input: AgentRunInput[RuntimeToolT],
    host: LoopHost[RuntimeToolT],
) -> AgentRunResult:
    """Run the think -> call-tools -> observe loop and return its outcome."""
    return ReactLoop(run_input, host).run()


__all__ = ["ReactLoop", "run_react_loop"]
