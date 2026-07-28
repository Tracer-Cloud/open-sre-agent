"""ReAct investigation agent — the core think → call tools → observe loop."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence
from typing import Any, cast

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from core import (
    RuntimeEventCallback,
    TupleEventCallback,
    execute_tools,
    summarise,
    tool_source,
)
from core.agent import Agent
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.llm_resolution import default_llm_factory
from core.context_budget import strip_internal_message_markers
from core.events import (
    MessageStartEvent,
    RuntimeEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from core.llm.factory import LLMRole, get_llm
from core.llm_invoke_errors import classify_llm_invoke_failure
from core.messages import (
    AssistantRuntimeMessage,
    MessageMapper,
    ProviderMessage,
    RuntimeMessage,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)
from core.provider import ProviderHooks, ProviderRequest
from core.state import InvestigationState
from core.state.evidence import EvidenceEntry
from platform.observability import debug_print
from platform.observability import get_progress_tracker as get_tracker
from platform.observability.trace.redaction import redact_sensitive
from tools.investigation.stages.gather_evidence.incident_command import (
    CONCLUSION_FORMAT_NUDGE,
    POST_TRIAGE_CHECKPOINT,
    incident_command_conclusion_complete,
)
from tools.investigation.stages.gather_evidence.loop import (
    InvestigationToolCallCache,
    degraded_investigation_from_llm_failure,
    duplicate_call_result,
    tool_call_signature,
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

_SEED_MARKER = "_opensre_seed"
_DUPLICATE_MARKER = "_opensre_duplicate_result"


def _wrap_llm_invoke_for_stagnation(
    llm: Any, *, should_withhold_tools: Callable[[], bool]
) -> Callable[[], None]:
    """Temporarily monkeypatch ``llm.invoke`` to withhold tool schemas once
    ``should_withhold_tools()`` is true, forcing a text-only response.

    ``react_loop.py`` fixes its tool schema list once at construction, so
    there is no per-iteration hook to drop tools after stagnation is
    detected without changing that file. Wrapping ``invoke`` itself is the
    only per-call seam available. ``get_llm()`` returns a cached, process-
    wide client, so the wrap MUST be reverted (the returned callable) in a
    ``finally`` block — never left in place for later, unrelated calls.
    """
    original_invoke = llm.invoke

    def _guarded_invoke(
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        active_tools = [] if should_withhold_tools() else tools
        return original_invoke(messages, system=system, tools=active_tools)

    llm.invoke = _guarded_invoke  # type: ignore[method-assign]

    def _restore() -> None:
        llm.invoke = original_invoke

    return _restore


def _is_duplicate_result_payload(value: Any) -> bool:
    return isinstance(value, dict) and value.get("suppressed_duplicate") is True


def _mark_duplicate_only_exchanges(messages: Sequence[RuntimeMessage]) -> list[RuntimeMessage]:
    """Tag each duplicate-only tool exchange with the context-budget eviction
    marker ``core.context_budget`` looks for, so it is deprioritized (evicted
    before real evidence) instead of trimmed by the generic heuristic alone.

    A ``ToolResultRuntimeMessage`` qualifies when every one of its results is
    a :func:`duplicate_call_result` payload; the preceding assistant message
    (which issued those now-duplicate calls) is tagged too, matching the
    pairing ``enforce_context_budget`` expects. ``react_loop.py`` builds
    these messages itself with no metadata, so they can only be reclassified
    here, after the fact, by inspecting their content.
    """
    marked = list(messages)
    for index, message in enumerate(marked):
        if not isinstance(message, ToolResultRuntimeMessage):
            continue
        if not message.results or not all(
            _is_duplicate_result_payload(result) for result in message.results
        ):
            continue
        marked[index] = dataclasses.replace(
            message, metadata={**message.metadata, _DUPLICATE_MARKER: True}
        )
        if index > 0 and isinstance(marked[index - 1], AssistantRuntimeMessage):
            previous = marked[index - 1]
            marked[index - 1] = dataclasses.replace(
                previous, metadata={**previous.metadata, _DUPLICATE_MARKER: True}
            )
    return marked


def _render_provider_messages_keeping_eviction_markers(
    llm: Any, messages: Sequence[RuntimeMessage]
) -> list[ProviderMessage]:
    """Render ``messages`` to provider dicts without stripping ``_opensre_*``
    eviction markers, unlike ``MessageMapper.to_provider_messages`` (which
    always strips them). ``enforce_context_budget`` needs to see the markers;
    stripping still happens, just later, right before the real provider call
    (see ``_strip_eviction_markers_before_request``) or, for the pipeline's
    persisted ``agent_messages``, at the ``diagnose`` stage.

    Renders one message at a time (rather than the whole batch) so a single
    ``RuntimeMessage``'s markers can be reapplied onto every provider dict it
    expands into — some providers (OpenAI-family) emit one tool-result dict
    per tool call from a single batched ``ToolResultRuntimeMessage``.
    """
    mapper = MessageMapper(llm)
    rendered: list[ProviderMessage] = []
    for message in messages:
        markers = {
            key: value for key, value in message.metadata.items() if key.startswith("_opensre_")
        }
        payloads = mapper.to_provider_messages([message])
        if markers:
            for payload in payloads:
                payload.update(markers)
        rendered.extend(payloads)
    return rendered


def _strip_eviction_markers_before_request(request: ProviderRequest) -> ProviderRequest:
    """Sanitize the outbound request: strict provider schemas (e.g. Anthropic)
    reject the ``_opensre_*`` eviction markers as unknown message keys."""
    return dataclasses.replace(
        request, messages=strip_internal_message_markers(list(request.messages))
    )


class ConnectedInvestigationAgent(Agent[Any]):
    """ReAct loop scoped to the tools enabled by connected integrations.

    Delegates the actual think -> call-tools -> observe mechanics to the
    shared ``core.agent.Agent`` loop, constructed via ``build_agent()``.
    Investigation-specific behavior (seed calls, duplicate-call suppression,
    stagnation handling, incident-command conclusion formatting) is layered
    on top via ``ToolExecutionHooks``, runtime-event listening, and the
    ``_should_accept_conclusion`` hook ``Agent`` already exposes — the same
    seam :class:`CLIBackedInvestigationAgent` overrides below.
    """

    _planned_actions: list[str]
    _current_evidence: dict[str, Any]
    _last_assistant_text: str = ""
    _conclusion_format_nudged: bool = False

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — used by overrides
        iteration: int,  # noqa: ARG002 — used by overrides
    ) -> tuple[bool, str | None]:
        """Decide what to do when the LLM stops requesting tools.

        Reject once when the final text omits required incident-command markers,
        so the model reformats before diagnose parses the conclusion.

        Override in subclasses (e.g. :class:`CLIBackedInvestigationAgent`) to
        nudge the model back into tool calls before accepting a conclusion.
        """
        last_text = getattr(self, "_last_assistant_text", "") or ""
        if (
            last_text.strip()
            and not incident_command_conclusion_complete(last_text)
            and not getattr(self, "_conclusion_format_nudged", False)
        ):
            self._conclusion_format_nudged = True
            return False, CONCLUSION_FORMAT_NUDGE
        return True, None

    def _build_system_prompt(self, state: dict[str, Any]) -> str:
        """Produce the LLM system prompt from an augmented state dict.

        Extension point for bench harnesses that need to swap the prompt body
        without reimplementing the state/tool_context merge.
        """
        return build_investigation_system_prompt(state)

    def run(  # type: ignore[override]
        self,
        state: InvestigationState,
        on_event: TupleEventCallback | None = None,
        on_runtime_event: RuntimeEventCallback | None = None,
    ) -> dict[str, Any]:
        """Run the full investigation. Returns a dict of state updates.

        Deliberately shadows ``Agent.run`` with a different signature: this is
        the investigation-pipeline entry point (``state`` in, state-update
        dict out), not the tool-calling loop itself. The actual loop runs on
        ``configured_agent`` below via ``Agent.run(configured_agent, ...)`` —
        an explicit unbound call, since ``configured_agent.run(...)`` would
        resolve back to this same overridden method.
        """
        tracker = get_tracker()
        tracker.start("investigation_agent", "Running investigation agent loop")

        state_dict = cast(dict[str, Any], state)
        resolved = dict(state.get("resolved_integrations") or {})
        available_tools = list(get_available_tools(resolved))
        tools = list(select_investigation_tools(available_tools, state_dict))
        tool_context = build_connected_tool_context(resolved, tools)
        tool_by_name = {tool.name: tool for tool in tools}

        if not tools:
            logger.warning("No tools available for investigation")

        llm = default_llm_factory()
        prompt_state = {**state_dict, **tool_context}
        system = self._build_system_prompt(prompt_state)
        alert_text = format_alert_context(prompt_state, tools)

        evidence: dict[str, Any] = {}
        evidence_entries: list[EvidenceEntry] = []
        executed_hypotheses: list[dict[str, Any]] = []
        tool_call_cache = InvestigationToolCallCache()

        def _emit(kind: str, data: dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                on_event(kind, data)
            except Exception:  # noqa: BLE001 - event rendering must never break the loop
                logger.debug("[runtime] on_event(%s) raised; ignoring", kind, exc_info=True)

        _emit(
            "agent_start",
            {
                "tool_count": len(tools),
                "connected_integrations": tool_context["connected_integrations"],
                "available_action_names": tool_context["available_action_names"],
            },
        )

        messages: list[Any] = [UserRuntimeMessage(content=alert_text)]

        seed_calls = build_seed_calls(state_dict, tools, llm)
        if seed_calls:
            logger.debug("[agent] seeding %d primary tool calls before LLM loop", len(seed_calls))
            for tc in seed_calls:
                tracker.record_tool_start(tc.name, redact_sensitive(tc.input), event_key=tc.id)
                _emit("tool_start", tool_event_payload(tc))
            executed_hypotheses.append(
                {
                    "hypothesis": "Seed primary integration tools",
                    "actions": [tc.name for tc in seed_calls],
                    "loop_iteration": -1,
                }
            )
            seed_results = execute_tools(seed_calls, tools, resolved)
            messages.append(
                AssistantRuntimeMessage(
                    content="",
                    tool_calls=tuple(seed_calls),
                    metadata={_SEED_MARKER: True},
                )
            )
            messages.append(
                ToolResultRuntimeMessage(
                    tool_calls=tuple(seed_calls),
                    results=tuple(seed_results),
                    metadata={_SEED_MARKER: True},
                )
            )
            for tc, output in zip(seed_calls, seed_results):
                tool_call_cache.store(tool_call_signature(tc), output, loop_iteration=-1)
                merge_tool_evidence(evidence, tc.name, output, tc.input)
                evidence_entries.append(
                    EvidenceEntry(
                        key=tc.name,
                        data=redact_sensitive(output),
                        tool_name=tc.name,
                        tool_args=redact_sensitive(tc.input),
                        source=tool_source(tool_by_name, tc.name),
                        loop_iteration=-1,
                    )
                )
                tracker.record_tool_end(
                    tc.name,
                    redact_sensitive(output),
                    event_key=tc.id,
                    tool_input=redact_sensitive(tc.input),
                )
                _emit("tool_end", tool_event_payload(tc, output=output))
                debug_print(f"[seed:{tc.name}] → {summarise(output)}")

        # Expose planned tools to the conclusion hook (used by
        # CLIBackedInvestigationAgent to require every planned tool be called
        # before accepting a text-only conclusion). Only names that exist in
        # the tool schema the LLM actually receives — a hallucinated or
        # expired name would otherwise cause futile nudge cycles.
        _available_tool_names = {t.name for t in tools}
        planned_actions = [
            str(name)
            for name in (state_dict.get("planned_actions") or [])
            if str(name).strip() and str(name) in _available_tool_names
        ]

        current_iteration = -1
        fresh_calls_this_iteration = 0
        stagnant_iterations = 0
        force_conclusion = False
        post_triage_checkpoint_sent = False

        def _before_tool_call(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
            nonlocal fresh_calls_this_iteration
            tc = request.tool_call
            signature = tool_call_signature(tc)
            cached = tool_call_cache.lookup(signature)
            if cached is None:
                fresh_calls_this_iteration += 1
                tracker.record_tool_start(tc.name, redact_sensitive(tc.input), event_key=tc.id)
                _emit("tool_start", tool_event_payload(tc))
                return None
            debug_print(f"[{tc.name}] → duplicate call suppressed")
            payload = duplicate_call_result(tc, cached)
            return BeforeToolCallResult(blocked=True, reason=payload["note"], details=payload)

        def _after_tool_call(
            request: ToolExecutionRequest,
            result: ToolExecutionResult,
        ) -> None:
            nonlocal post_triage_checkpoint_sent
            tc = request.tool_call
            output = result.compat_payload()
            tool_call_cache.store(tool_call_signature(tc), output, loop_iteration=current_iteration)
            merge_tool_evidence(evidence, tc.name, output, tc.input)
            evidence_entries.append(
                EvidenceEntry(
                    key=tc.name,
                    data=redact_sensitive(output),
                    tool_name=tc.name,
                    tool_args=redact_sensitive(tc.input),
                    source=tool_source(tool_by_name, tc.name),
                    loop_iteration=current_iteration,
                )
            )
            tracker.record_tool_end(
                tc.name,
                redact_sensitive(output),
                event_key=tc.id,
                tool_input=redact_sensitive(tc.input),
            )
            _emit("tool_end", tool_event_payload(tc, output=output))
            debug_print(f"[{tc.name}] → {summarise(output)}")
            if current_iteration == 0 and not post_triage_checkpoint_sent:
                configured_agent.steer(POST_TRIAGE_CHECKPOINT)
                post_triage_checkpoint_sent = True

        def _on_investigation_runtime_event(event: RuntimeEvent) -> None:
            nonlocal current_iteration, fresh_calls_this_iteration, stagnant_iterations
            nonlocal force_conclusion
            if isinstance(event, TurnStartEvent):
                current_iteration = event.iteration
                fresh_calls_this_iteration = 0
                _emit("llm_start", {"iteration": event.iteration})
            elif isinstance(event, MessageStartEvent):
                content = getattr(event.message, "content", "")
                configured_agent._last_assistant_text = str(content or "")
            elif isinstance(event, TurnEndEvent) and event.tool_results:
                if fresh_calls_this_iteration == 0:
                    stagnant_iterations += 1
                    configured_agent.steer(STAGNATION_NUDGE)
                    if stagnant_iterations >= MAX_STAGNANT_ITERATIONS:
                        logger.warning(
                            "[agent] %d consecutive duplicate-only iterations — closing "
                            "tool access before MAX_INVESTIGATION_LOOPS",
                            stagnant_iterations,
                        )
                        force_conclusion = True
                else:
                    stagnant_iterations = 0
            if on_runtime_event is not None:
                try:
                    on_runtime_event(event)
                except Exception:  # noqa: BLE001 - event rendering must never break the loop
                    logger.debug("[runtime] on_runtime_event raised; ignoring", exc_info=True)

        config = AgentConfig(
            llm=llm,
            system=system,
            tools=tuple(tools),
            resolved_integrations=resolved,
            max_iterations=MAX_INVESTIGATION_LOOPS,
            tool_hooks=ToolExecutionHooks(
                before_tool_call=_before_tool_call,
                after_tool_call=_after_tool_call,
            ),
            on_runtime_event=_on_investigation_runtime_event,
            agent_cls=type(self),
            provider_hooks=ProviderHooks(
                transform_messages=_mark_duplicate_only_exchanges,
                convert_to_llm=_render_provider_messages_keeping_eviction_markers,
                before_provider_request=_strip_eviction_markers_before_request,
            ),
        )
        configured_agent = cast(ConnectedInvestigationAgent, build_agent(config))
        configured_agent._planned_actions = planned_actions
        configured_agent._current_evidence = evidence
        configured_agent._last_assistant_text = ""
        configured_agent._conclusion_format_nudged = False

        # get_llm() returns a cached, process-wide client, so this guard is
        # applied only for the duration of this run and always reverted.
        restore_invoke = _wrap_llm_invoke_for_stagnation(
            llm, should_withhold_tools=lambda: force_conclusion
        )
        try:
            # Explicit unbound call: configured_agent.run(...) would resolve
            # back to ConnectedInvestigationAgent.run (this method) instead
            # of the tool-calling loop, since it shares the same class.
            result = Agent.run(configured_agent, initial_messages=messages)
        except Exception as err:
            failure = classify_llm_invoke_failure(err)
            if failure is None:
                raise
            return degraded_investigation_from_llm_failure(
                failure,
                err=err,
                tracker=tracker,
                _emit=_emit,
                evidence=evidence,
                evidence_entries=evidence_entries,
                messages=_render_provider_messages_keeping_eviction_markers(llm, messages),
                executed_hypotheses=executed_hypotheses,
                tool_context=tool_context,
                # react_loop.py marks an iteration "used" the moment it starts,
                # before the (now-failing) invoke() resolves, so the failing
                # attempt itself must not be counted as a completed loop.
                investigation_loop_count=max(0, configured_agent._react_iterations_used - 1),
            )
        finally:
            restore_invoke()

        loops_completed = result.llm_iterations_used
        if result.hit_iteration_cap:
            logger.warning(
                "[agent] hit MAX_INVESTIGATION_LOOPS=%d without finishing",
                MAX_INVESTIGATION_LOOPS,
            )

        # Rebuild one hypothesis entry per real loop iteration (seed calls
        # already recorded their own hypothesis above) from the fresh
        # (non-duplicate) tool calls executed that iteration, matching the
        # per-iteration grouping the old hand-rolled loop produced inline.
        actions_by_iteration: dict[int, list[str]] = {}
        for entry in evidence_entries:
            if entry.loop_iteration < 0:
                continue
            actions_by_iteration.setdefault(entry.loop_iteration, []).append(entry.tool_name)
        for iteration in sorted(actions_by_iteration):
            executed_hypotheses.append(
                {
                    "hypothesis": f"Agent iteration {iteration}",
                    "actions": actions_by_iteration[iteration],
                    "loop_iteration": iteration,
                }
            )

        # transform_messages only ran transiently per-iteration inside the loop
        # (react_loop.py never writes it back to the canonical transcript), so
        # duplicate exchanges are reclassified here, once, before the final
        # render — agent_messages still carries the eviction markers on return;
        # diagnose() is the stage that strips them before persisting state.
        final_messages = _mark_duplicate_only_exchanges(result.messages)
        agent_messages = _render_provider_messages_keeping_eviction_markers(llm, final_messages)

        _emit(
            "agent_end",
            {
                "evidence_count": len(evidence_entries),
                "message_count": len(agent_messages),
                "investigation_loop_count": loops_completed,
                "investigation_iteration_cap": MAX_INVESTIGATION_LOOPS,
            },
        )

        tracker.complete(
            "investigation_agent",
            fields_updated=["evidence", "evidence_entries", "agent_messages"],
            message=f"evidence:{len(evidence_entries)} messages:{len(agent_messages)}",
        )

        updates = {
            "evidence": evidence,
            "evidence_entries": [e.model_dump() for e in evidence_entries],
            "agent_messages": agent_messages,
            "executed_hypotheses": executed_hypotheses,
            "investigation_loop_count": loops_completed,
            "investigation_iteration_cap": MAX_INVESTIGATION_LOOPS,
        }
        updates.update(tool_context)
        return updates


InvestigationAgent = ConnectedInvestigationAgent


def get_investigation_agent_class() -> type[ConnectedInvestigationAgent]:
    """Return the investigation agent class appropriate for the current LLM provider.

    Callers that need a fixed class (e.g. bench harness, integration tests) should
    pass an explicit ``agent_class`` to the pipeline rather than calling this.
    """
    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient

    if isinstance(get_llm(LLMRole.AGENT), CLIBackedAgentClient):
        return CLIBackedInvestigationAgent
    return ConnectedInvestigationAgent


class CLIBackedInvestigationAgent(ConnectedInvestigationAgent):
    """Investigation agent for CLI-backed LLMs (Codex, Claude Code CLI, etc.).

    CLI models receive the full conversation history flattened into a single
    text prompt per invoke. They tend to emit a plain-text final answer as
    soon as they see accumulated tool results, exiting the ReAct loop before
    all planned tools have been called.

    This subclass overrides the conclusion hook to nudge the model to call
    every planned tool before accepting its final answer. The outer
    MAX_INVESTIGATION_LOOPS cap still bounds worst-case runtime.
    """

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — base class signature
        iteration: int,
    ) -> tuple[bool, str | None]:
        planned = getattr(self, "_planned_actions", [])
        evidence = getattr(self, "_current_evidence", None)

        if not planned or evidence is None:
            return super()._should_accept_conclusion(
                evidence_count=evidence_count,
                iteration=iteration,
            )

        # Leave room for a final text-only iteration after the nudge fires.
        if iteration >= MAX_INVESTIGATION_LOOPS - 2:
            return super()._should_accept_conclusion(
                evidence_count=evidence_count,
                iteration=iteration,
            )

        uncalled = [name for name in planned if name not in evidence]
        if not uncalled:
            return super()._should_accept_conclusion(
                evidence_count=evidence_count,
                iteration=iteration,
            )

        tool_list = ", ".join(uncalled)
        return False, (
            f"You have not yet called these planned investigation tools: {tool_list}. "
            "Call them now using the JSON tool_calls format before writing your final answer."
        )
