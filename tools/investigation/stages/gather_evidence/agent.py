"""Investigation orchestration over the shared tool-calling ``Agent`` runtime."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from core import RuntimeEventCallback, TupleEventCallback, execute_tools, summarise, tool_source
from core.agent.goals import Goal, GoalObservation
from core.agent_harness.runtime import AgentConfig, build_agent, default_llm_factory
from core.events import (
    AgentEndEvent,
    AgentStartEvent,
    ProviderRequestEndEvent,
    RuntimeEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
    runtime_event_from_tuple,
    tuple_payload_from_event,
)
from core.llm.types import ToolCall
from core.llm_invoke_errors import classify_llm_invoke_failure
from core.messages import MessageMapper, RuntimeMessage, ToolResultRuntimeMessage
from core.provider import ProviderHooks, ProviderRequest
from core.state import InvestigationState
from core.state.evidence import EvidenceEntry
from core.tool import RegisteredTool, RuntimeTool
from infrastructure.observability import debug_print
from infrastructure.observability import get_progress_tracker as get_tracker
from infrastructure.observability.trace.redaction import (
    RedactedToolView,
    redact_sensitive,
    redact_tool_view,
)
from tools.investigation.stages.gather_evidence.incident_command import (
    CONCLUSION_FORMAT_NUDGE,
    POST_TRIAGE_CHECKPOINT,
    incident_command_conclusion_complete,
)
from tools.investigation.stages.gather_evidence.loop import (
    InvestigationLoopController,
    degraded_investigation_from_llm_failure,
)
from tools.investigation.stages.gather_evidence.prompt import (
    build_investigation_system_prompt,
    format_alert_context,
)
from tools.investigation.stages.gather_evidence.tools import (
    MAX_STAGNANT_ITERATIONS,
    STAGNATION_NUDGE,
    build_connected_tool_context,
    build_seed_calls,
    get_available_tools,
    merge_tool_evidence,
    select_investigation_tools,
    tool_event_payload,
)

logger = logging.getLogger(__name__)

LlmFactory = Callable[[], Any]


@dataclass
class _ConclusionState:
    nudge: str | None = None


@dataclass
class _RuntimeState:
    controller: InvestigationLoopController
    tracker: Any
    tool_context: dict[str, Any]
    evidence_entries: list[EvidenceEntry]
    loops_completed: int = 0
    last_messages: list[RuntimeMessage] | None = None


class ConnectedInvestigationAgent:
    """Run the investigation policy through ``AgentConfig`` and ``build_agent``.

    Seed evidence, tool selection, duplicate suppression, and conclusion policy
    remain investigation-owned; the think -> call-tools -> observe loop is the
    shared :class:`core.agent.Agent` implementation.
    """

    def __init__(self, *, llm_factory: LlmFactory | None = None) -> None:
        self._llm_factory = llm_factory
        self._on_tuple_event: TupleEventCallback | None = None
        self._on_runtime_event: RuntimeEventCallback | None = None

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — used by overrides
        iteration: int,  # noqa: ARG002 — used by overrides
        final_text: str = "",
    ) -> tuple[bool, str | None]:
        """Accept complete incident-command output, nudging incomplete output once."""
        last_text = final_text or getattr(self, "_last_assistant_text", "") or ""
        if (
            last_text.strip()
            and not incident_command_conclusion_complete(last_text)
            and not getattr(self, "_conclusion_format_nudged", False)
        ):
            self._conclusion_format_nudged = True
            return False, CONCLUSION_FORMAT_NUDGE
        return True, None

    def _build_system_prompt(self, state: dict[str, Any]) -> str:
        """Produce the investigation system prompt from augmented state."""
        return build_investigation_system_prompt(state)

    def _filter_tools[RuntimeToolT: RuntimeTool](
        self, tools: list[RuntimeToolT]
    ) -> list[RuntimeToolT]:
        """Return the tools exposed to the investigation; subclasses may narrow them."""
        return tools

    def _dispatch_runtime(self, event: RuntimeEvent) -> None:
        if self._on_runtime_event is not None:
            try:
                self._on_runtime_event(event)
            except Exception:  # noqa: BLE001 - observers must not break investigations
                logger.debug("runtime investigation observer failed", exc_info=True)
        payload = tuple_payload_from_event(event)
        if payload is not None and self._on_tuple_event is not None:
            try:
                self._on_tuple_event(*payload)
            except Exception:  # noqa: BLE001 - observers must not break investigations
                logger.debug("tuple investigation observer failed", exc_info=True)

    def _dispatch_tuple(self, kind: str, data: dict[str, Any]) -> None:
        event = runtime_event_from_tuple(kind, data)
        if event is not None:
            self._dispatch_runtime(event)
        elif self._on_tuple_event is not None:
            try:
                self._on_tuple_event(kind, data)
            except Exception:  # noqa: BLE001 - observers must not break investigations
                logger.debug("tuple investigation observer failed", exc_info=True)

    def _record_seed_start(self, tool_call: ToolCall) -> RedactedToolView:
        redacted = redact_tool_view(tool_call.input)
        self._tracker.record_tool_start(
            tool_call.name,
            redacted.tool_input,
            event_key=tool_call.id,
        )
        self._dispatch_tuple("tool_start", tool_event_payload(tool_call, redacted=redacted))
        return redacted

    def _record_seed_end(
        self,
        tool_call: ToolCall,
        output: Any,
        *,
        redacted_input: RedactedToolView,
    ) -> RedactedToolView:
        redacted = RedactedToolView(
            tool_input=redacted_input.tool_input,
            output=redact_sensitive(output),
        )
        self._tracker.record_tool_end(
            tool_call.name,
            redacted.output,
            event_key=tool_call.id,
            tool_input=redacted.tool_input,
        )
        self._dispatch_tuple(
            "tool_end",
            tool_event_payload(tool_call, output=output, redacted=redacted),
        )
        return redacted

    def run(
        self,
        state: InvestigationState,
        on_event: TupleEventCallback | None = None,
        on_runtime_event: RuntimeEventCallback | None = None,
    ) -> dict[str, Any]:
        """Run one investigation and return partial state updates."""
        self._on_tuple_event = on_event
        self._on_runtime_event = on_runtime_event
        self._tracker = get_tracker()
        self._tracker.start("investigation_agent", "Running investigation agent loop")

        state_dict = cast(dict[str, Any], state)
        resolved = dict(state.get("resolved_integrations") or {})
        available_tools = list(self._filter_tools(list(get_available_tools(resolved))))
        tools = list(select_investigation_tools(available_tools, state_dict))
        tool_context = build_connected_tool_context(resolved, tools)
        tool_by_name = {tool.name: tool for tool in tools}
        if not tools:
            logger.warning("No tools available for investigation")

        llm = (self._llm_factory or default_llm_factory)()
        prompt_state = {**state_dict, **tool_context}
        system = self._build_system_prompt(prompt_state)
        alert_text = format_alert_context(prompt_state, tools)
        messages: list[dict[str, Any]] = [{"role": "user", "content": alert_text}]
        evidence: dict[str, Any] = {}
        evidence_entries: list[EvidenceEntry] = []
        controller = InvestigationLoopController(
            stagnation_nudge=STAGNATION_NUDGE,
            checkpoint_nudge=POST_TRIAGE_CHECKPOINT,
            max_stagnant_iterations=MAX_STAGNANT_ITERATIONS,
        )
        runtime_state = _RuntimeState(
            controller=controller,
            tracker=self._tracker,
            tool_context=tool_context,
            evidence_entries=evidence_entries,
        )

        self._dispatch_tuple(
            "agent_start",
            {
                "tool_count": len(tools),
                "connected_integrations": tool_context["connected_integrations"],
                "available_action_names": tool_context["available_action_names"],
            },
        )
        self._run_seed_calls(
            state_dict=state_dict,
            llm=llm,
            tools=tools,
            resolved=resolved,
            tool_by_name=tool_by_name,
            controller=controller,
            messages=messages,
            evidence=evidence,
            evidence_entries=evidence_entries,
        )

        available_tool_names = {tool.name for tool in tools}
        self._planned_actions = [
            str(name)
            for name in (state_dict.get("planned_actions") or [])
            if str(name).strip() and str(name) in available_tool_names
        ]
        self._current_evidence = controller.current_evidence
        for name, value in evidence.items():
            if name != "tool_outputs":
                self._current_evidence[name] = value
        self._last_assistant_text = ""
        self._conclusion_format_nudged = False

        conclusion_state = _ConclusionState()
        goal = self._build_goal(conclusion_state)
        on_shared_event = self._build_runtime_observer(runtime_state)
        provider_hooks = self._build_provider_hooks(runtime_state)
        config = AgentConfig(
            llm=llm,
            system=system,
            tools=tuple(tools),
            resolved_integrations=resolved,
            max_iterations=MAX_INVESTIGATION_LOOPS,
            tool_hooks=controller.hooks(),
            provider_hooks=provider_hooks,
            on_runtime_event=on_shared_event,
            goal=goal,
        )
        shared_agent = build_agent(config)
        controller.bind_queue_message(shared_agent.steer)

        try:
            run_result = shared_agent.run(messages)
        except Exception as err:
            failure = classify_llm_invoke_failure(err)
            if failure is None:
                raise
            self._merge_fresh_results(
                controller,
                evidence=evidence,
                evidence_entries=evidence_entries,
                tool_by_name=tool_by_name,
            )
            partial_messages = self._provider_messages(
                llm,
                runtime_state.last_messages or [],
                controller,
            )
            return degraded_investigation_from_llm_failure(
                failure,
                err=err,
                tracker=self._tracker,
                _emit=self._dispatch_tuple,
                evidence=evidence,
                evidence_entries=evidence_entries,
                messages=partial_messages,
                executed_hypotheses=controller.executed_hypotheses,
                tool_context=tool_context,
                investigation_loop_count=runtime_state.loops_completed,
            )

        self._merge_fresh_results(
            controller,
            evidence=evidence,
            evidence_entries=evidence_entries,
            tool_by_name=tool_by_name,
        )
        provider_messages = self._provider_messages(llm, run_result.messages, controller)
        self._tracker.complete(
            "investigation_agent",
            fields_updated=["evidence", "evidence_entries", "agent_messages"],
            message=f"evidence:{len(evidence_entries)} messages:{len(provider_messages)}",
        )
        updates = {
            "evidence": evidence,
            "evidence_entries": [entry.model_dump() for entry in evidence_entries],
            "agent_messages": provider_messages,
            "executed_hypotheses": controller.executed_hypotheses,
            "investigation_loop_count": run_result.llm_iterations_used,
            "investigation_iteration_cap": MAX_INVESTIGATION_LOOPS,
        }
        updates.update(tool_context)
        return updates

    def _build_goal(self, state: _ConclusionState) -> Goal:
        def _verify(observation: GoalObservation) -> bool:
            self._last_assistant_text = observation.final_text
            accept, nudge = self._should_accept_conclusion(
                evidence_count=len(self._current_evidence),
                iteration=observation.iteration,
                final_text=observation.final_text,
            )
            if not accept and nudge is None:
                raise ValueError(
                    f"{type(self).__name__}._should_accept_conclusion returned "
                    "(False, None) — a nudge string is required when rejecting "
                    "the conclusion."
                )
            state.nudge = nudge
            return accept

        def _nudge(_observation: GoalObservation) -> str:
            if state.nudge is None:
                raise ValueError("Investigation conclusion was rejected without a nudge.")
            return state.nudge

        return Goal(
            description="complete the investigation",
            success_criteria="the investigation-specific conclusion policy accepts the answer",
            verify=_verify,
            nudge=_nudge,
        )

    def _build_runtime_observer(
        self,
        state: _RuntimeState,
    ) -> RuntimeEventCallback:
        def _observe(event: RuntimeEvent) -> None:
            if isinstance(event, AgentStartEvent):
                return
            if isinstance(event, TurnStartEvent):
                state.controller.begin_iteration(event.iteration)
            elif isinstance(event, ProviderRequestEndEvent):
                state.loops_completed = max(state.loops_completed, event.iteration + 1)
            elif isinstance(event, ToolExecutionStartEvent):
                if state.controller.is_duplicate_event(
                    iteration=event.iteration,
                    tool_call_id=event.tool_call_id,
                ):
                    return
                state.tracker.record_tool_start(
                    event.tool_name,
                    event.args,
                    event_key=event.tool_call_id,
                )
            elif isinstance(event, ToolExecutionEndEvent):
                state.controller.record_runtime_result(
                    iteration=event.iteration,
                    tool_call_id=event.tool_call_id,
                    result=event.result,
                )
                if state.controller.is_duplicate_event(
                    iteration=event.iteration,
                    tool_call_id=event.tool_call_id,
                ):
                    return
                state.tracker.record_tool_end(
                    event.tool_name,
                    event.result,
                    event_key=event.tool_call_id,
                    tool_input=event.args,
                )
                debug_print(f"[{event.tool_name}] → {summarise(event.result)}")
            elif isinstance(event, AgentEndEvent):
                event = replace(
                    event,
                    data={
                        **event.data,
                        "evidence_count": len(state.controller.fresh_results())
                        + len(state.evidence_entries),
                        "investigation_loop_count": state.loops_completed,
                        "investigation_iteration_cap": MAX_INVESTIGATION_LOOPS,
                    },
                )
            self._dispatch_runtime(event)

        return _observe

    @staticmethod
    def _build_provider_hooks(state: _RuntimeState) -> ProviderHooks:
        def _capture(messages: Sequence[RuntimeMessage]) -> Sequence[RuntimeMessage]:
            state.last_messages = list(messages)
            return messages

        def _force_conclusion(request: ProviderRequest) -> ProviderRequest:
            if not state.controller.force_conclusion:
                return request
            logger.warning(
                "[agent] repeated duplicate-only iterations — forcing tool-free conclusion"
            )
            return replace(request, tools=[])

        def _classify_tool_calls(request: ProviderRequest, response: Any) -> Any:
            tool_calls = tuple(getattr(response, "tool_calls", ()) or ())
            if tool_calls:
                state.controller.prepare_batch(
                    tool_calls,
                    iteration=int(request.metadata.get("iteration", 0)),
                )
            return response

        return ProviderHooks(
            transform_messages=_capture,
            before_provider_request=_force_conclusion,
            after_provider_response=_classify_tool_calls,
        )

    def _run_seed_calls(
        self,
        *,
        state_dict: dict[str, Any],
        llm: Any,
        tools: list[RegisteredTool],
        resolved: dict[str, Any],
        tool_by_name: Mapping[str, RegisteredTool],
        controller: InvestigationLoopController,
        messages: list[dict[str, Any]],
        evidence: dict[str, Any],
        evidence_entries: list[EvidenceEntry],
    ) -> None:
        seed_calls = build_seed_calls(state_dict, tools, llm)
        if not seed_calls:
            return
        logger.debug("[agent] seeding %d primary tool calls before LLM loop", len(seed_calls))
        redacted_inputs = [self._record_seed_start(tool_call) for tool_call in seed_calls]
        controller.executed_hypotheses.append(
            {
                "hypothesis": "Seed primary integration tools",
                "actions": [tool_call.name for tool_call in seed_calls],
                "loop_iteration": -1,
            }
        )
        seed_results = execute_tools(seed_calls, tools, resolved)
        mapper = MessageMapper(llm)
        seed_assistant = mapper.to_synthetic_assistant_provider_message(seed_calls)
        seed_messages = mapper.to_tool_result_provider_messages(seed_calls, seed_results)
        for message in [seed_assistant, *seed_messages]:
            message["_opensre_seed"] = True
        messages.extend([seed_assistant, *seed_messages])

        for tool_call, output, redacted_input in zip(
            seed_calls,
            seed_results,
            redacted_inputs,
            strict=True,
        ):
            controller.record_seed(tool_call, output)
            redacted = self._record_seed_end(
                tool_call,
                output,
                redacted_input=redacted_input,
            )
            merge_tool_evidence(
                evidence,
                tool_call.name,
                output,
                tool_call.input,
                redacted=redacted,
            )
            evidence_entries.append(
                EvidenceEntry(
                    key=tool_call.name,
                    data=redacted.output,
                    tool_name=tool_call.name,
                    tool_args=redacted.tool_input,
                    source=tool_source(tool_by_name, tool_call.name),
                    loop_iteration=-1,
                )
            )
            debug_print(f"[seed:{tool_call.name}] → {summarise(output)}")

    @staticmethod
    def _merge_fresh_results(
        controller: InvestigationLoopController,
        *,
        evidence: dict[str, Any],
        evidence_entries: list[EvidenceEntry],
        tool_by_name: Mapping[str, RegisteredTool],
    ) -> None:
        for tool_call, output in controller.fresh_results():
            redacted = redact_tool_view(tool_call.input, output)
            merge_tool_evidence(
                evidence,
                tool_call.name,
                output,
                tool_call.input,
                redacted=redacted,
            )
            evidence_entries.append(
                EvidenceEntry(
                    key=tool_call.name,
                    data=redacted.output,
                    tool_name=tool_call.name,
                    tool_args=redacted.tool_input,
                    source=tool_source(tool_by_name, tool_call.name),
                    loop_iteration=controller.iteration_for(tool_call),
                )
            )

    @staticmethod
    def _provider_messages(
        llm: Any,
        messages: Sequence[RuntimeMessage],
        controller: InvestigationLoopController,
    ) -> list[dict[str, Any]]:
        mapper = MessageMapper(llm)
        provider_messages: list[dict[str, Any]] = []
        for message in messages:
            metadata = dict(getattr(message, "metadata", {}) or {})
            tool_calls = tuple(getattr(message, "tool_calls", ()) or ())
            if tool_calls and all(controller.was_suppressed(call) for call in tool_calls):
                metadata["_opensre_duplicate_result"] = True
            if isinstance(message, ToolResultRuntimeMessage) and metadata.get(
                "_opensre_duplicate_result"
            ):
                duplicate_payloads = [controller.duplicate_payload(call) for call in tool_calls]
                rendered = mapper.to_tool_result_provider_messages(
                    list(tool_calls),
                    [payload for payload in duplicate_payloads if payload is not None],
                )
            else:
                rendered = mapper.to_provider_messages([message])
            for provider_message in rendered:
                provider_messages.append({**provider_message, **metadata})
        return provider_messages


InvestigationAgent = ConnectedInvestigationAgent


def get_investigation_agent_class(
    *, cli_backed: bool | None = None
) -> type[ConnectedInvestigationAgent]:
    """Return the investigation policy for a CLI-backed vs hosted agent LLM.

    When ``cli_backed`` is omitted, routing is read from configuration. Callers
    that already know the transport pass the flag so they do not re-resolve it.
    """
    if cli_backed is None:
        from core.agent_harness.runtime import agent_llm_is_cli_backed

        cli_backed = agent_llm_is_cli_backed()
    if cli_backed:
        return CLIBackedInvestigationAgent
    return ConnectedInvestigationAgent


class CLIBackedInvestigationAgent(ConnectedInvestigationAgent):
    """Require CLI-backed models to call every planned tool before concluding."""

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — base class signature
        iteration: int,
        final_text: str = "",
    ) -> tuple[bool, str | None]:
        planned = getattr(self, "_planned_actions", [])
        evidence = getattr(self, "_current_evidence", None)
        if not planned or evidence is None or iteration >= MAX_INVESTIGATION_LOOPS - 2:
            return super()._should_accept_conclusion(
                evidence_count=evidence_count,
                iteration=iteration,
                final_text=final_text,
            )
        uncalled = [name for name in planned if name not in evidence]
        if not uncalled:
            return super()._should_accept_conclusion(
                evidence_count=evidence_count,
                iteration=iteration,
                final_text=final_text,
            )
        return False, (
            f"You have not yet called these planned investigation tools: {', '.join(uncalled)}. "
            "Call them now using the JSON tool_calls format before writing your final answer."
        )


__all__ = [
    "CLIBackedInvestigationAgent",
    "ConnectedInvestigationAgent",
    "InvestigationAgent",
    "get_investigation_agent_class",
]
