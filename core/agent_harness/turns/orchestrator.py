"""Decoupled turn engine: three-path routing + conversational assistant.

This is the surface-agnostic heart of the turn harness, lifted out of the
interactive shell. It owns:

* ``stream_answer`` — one no-tool streamed answer from the grounded
  conversational assistant (guidance only; no investigation run). A single
  streaming LLM call with no ReAct loop and no tool use. Records the exchange.
* ``run_turn`` — the three-path routing (summarize-observation / handled /
  gather+answer) that sequences the action driver, the gather pass, and the
  answer path.

All terminal/session/grounding/telemetry concerns are reached through the
Protocols in :mod:`core.agent_harness.ports`. Nothing here imports ``interactive_shell``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from config.llm_reasoning_effort import apply_reasoning_effort
from core.agent_harness.ports import (
    AnswerRequest,
    ConfirmFn,
    ErrorReporter,
    EvidenceGatherer,
    ExecuteActions,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    SessionStore,
    StreamAnswerFn,
    TurnAccounting,
)
from core.agent_harness.prompts.assistant import (
    AssistantTurnPrompt,
    build_cli_agent_turn_prompt,
)
from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
from core.agent_harness.prompts.memory.prior_investigation import is_prior_investigation_follow_up
from core.agent_harness.session.terminal_access import agent_turn_executed_slashes
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.agent_harness.turns.transcript_compaction import auto_compact_if_needed
from core.agent_harness.turns.turn_plan import TurnPlan, build_turn_plan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.llm_invoke_errors import is_cli_timeout_error
from platform.observability.trace.spans import component_span, emit_route

log = logging.getLogger(__name__)

_ASSISTANT_LABEL = "assistant"


# ---------------------------------------------------------------------------
# Conversational assistant answer (interpreter edge for one turn)
# ---------------------------------------------------------------------------


def stage_turn_error(session: Any, kind: str, message: str) -> None:
    """Best-effort structured error staging for the turn's telemetry flush."""
    # Analytics staging lives on the shell terminal facet; other sessions have none.
    terminal = getattr(session, "terminal", None)
    setter = getattr(terminal, "set_pending_turn_error", None)
    if callable(setter):
        setter(kind, message)


def stage_turn_llm_failure(session: Any, *, client: Any | None = None) -> None:
    """Best-effort staging of the attempted conversational-LLM identity.

    When the LLM was the intended route for a turn but the provider failed,
    the turn's ``$ai_model`` must reflect the attempted model (or ``unknown``)
    rather than the terminal-action sentinel. Stages whatever identity the
    failed *client* exposes; when nothing resolves, the recorder falls back to
    ``unknown`` based on the staged error kind.
    """
    from core.agent_harness.accounting.token_accounting import (
        LlmRunInfo,
        resolve_model_name,
        resolve_provider_name,
    )

    terminal = getattr(session, "terminal", None)
    setter = getattr(terminal, "set_pending_turn_llm", None)
    if not callable(setter):
        return
    model = resolve_model_name(client) if client is not None else None
    provider = resolve_provider_name(client) if client is not None else None
    if model or provider:
        setter(LlmRunInfo(model=model, provider=provider))


def _stream_response(
    *,
    client: Any,
    turn_prompt: AssistantTurnPrompt,
    output: OutputSink,
    run_factory: RunRecordFactory,
    error_reporter: ErrorReporter | None,
    session: Any | None = None,
) -> Any | None:
    try:
        started = time.monotonic()
        text_str = output.stream(
            label=_ASSISTANT_LABEL,
            chunks=client.invoke_stream(turn_prompt.messages()),
        )
    except KeyboardInterrupt:
        output.print("· cancelled")
        return None
    except Exception as exc:
        if error_reporter is not None:
            error_reporter.report(
                exc,
                context="core.agent_harness.turns.orchestrator.stream",
                expected=is_cli_timeout_error(exc),
            )
        if session is not None:
            kind = "llm_timeout" if is_cli_timeout_error(exc) else "assistant_error"
            stage_turn_error(session, kind, str(exc))
            stage_turn_llm_failure(session, client=client)
        output.render_error(f"assistant failed: {exc}")
        return None
    return run_factory.build(
        client=client,
        prompt=turn_prompt.joined(),
        response_text=text_str,
        started=started,
    )


def _record_answer_turn(session: SessionStore, message: str, assistant_text: str) -> None:
    record_conversation_turn(session, message, assistant_text)


def _record_action_only_turn(session: SessionStore, message: str, assistant_text: str) -> None:
    text = assistant_text.strip()
    if not text:
        return
    latest = session.cli_agent_messages[-2:]
    if latest == [("user", message), ("assistant", text)]:
        return
    _record_answer_turn(session, message, assistant_text)


def stream_answer(
    # Direct answer (no tools) shared by the interactive shell and headless surfaces.
    message: str,
    session: SessionStore,
    output: OutputSink,
    *,
    prompts: PromptContextProvider,
    reasoning: ReasoningClientProvider,
    run_factory: RunRecordFactory,
    error_reporter: ErrorReporter | None = None,
    request: AnswerRequest | None = None,
) -> Any | None:
    """Stream one grounded conversational answer (guidance only, no tools).

    The **direct answer** path (no tools): a single ``invoke_stream`` call with
    no ReAct loop. The **tool-calling** agent is ``core.agent.Agent`` — see
    ``core/agent_harness/AGENTS.md``.

    ``request.turn_plan`` is the turn-wide assembly built at turn start. Its
    snapshot (conversation history, integration state, prior investigation,
    synthetic-run path) grounds prompt construction in a stable turn-start view
    rather than the live session.
    """
    req = request if request is not None else AnswerRequest()
    client = reasoning.get()
    if client is None:
        return None

    turn_plan = req.turn_plan
    ctx = (
        turn_plan.snapshot
        if turn_plan is not None
        else TurnSnapshot.from_session(message, session, surface=prompts.surface())
    )

    turn_prompt = build_cli_agent_turn_prompt(
        message=message,
        prompts=prompts,
        tool_observation=req.tool_observation,
        tool_observation_on_screen=req.tool_observation_on_screen,
        handoff_contents=req.handoff_contents,
        turn_snapshot=ctx,
    )

    run = _stream_response(
        client=client,
        turn_prompt=turn_prompt,
        output=output,
        run_factory=run_factory,
        error_reporter=error_reporter,
        session=session,
    )
    if run is None:
        return None

    text_str = getattr(run, "response_text", "") or ""
    _record_answer_turn(session, message, text_str)

    return run


# ---------------------------------------------------------------------------
# Turn routing (pure router + snapshot adapter) and orchestration
# ---------------------------------------------------------------------------


def _response_text(run: Any | None) -> str:
    text = getattr(run, "response_text", "") if run is not None else ""
    return text or ""


@dataclass(frozen=True)
class TurnRoutingInput:
    """Minimal facts the turn router decides on, snapshotted from the world."""

    action_handled: bool
    executed_success_count: int
    has_observation: bool
    investigation_dispatched: bool = False


@dataclass(frozen=True)
class TurnRoute:
    """The chosen turn path."""

    intent: Literal["summarize_observation", "handled_without_llm", "gather_and_answer"]


def _is_literal_slash_command(text: str) -> bool:
    """True when the user submitted an explicit ``/slash`` command line."""
    return text.strip().startswith("/")


def _route_turn(
    routing: TurnRoutingInput,
    *,
    user_text: str = "",
    handoff_contents: tuple[str, ...] = (),
) -> TurnRoute:
    """Decide the turn path from routing facts (pure)."""
    if (
        routing.investigation_dispatched
        and routing.action_handled
        and not _is_literal_slash_command(user_text)
    ):
        if routing.has_observation and routing.executed_success_count > 0:
            return TurnRoute(intent="summarize_observation")
        return TurnRoute(intent="handled_without_llm")
    if (
        routing.action_handled
        and routing.has_observation
        and routing.executed_success_count > 0
        and not _is_literal_slash_command(user_text)
    ):
        return TurnRoute(intent="summarize_observation")
    if routing.action_handled and not handoff_contents:
        return TurnRoute(intent="handled_without_llm")
    return TurnRoute(intent="gather_and_answer")


def _routing_input_from_result(
    action_result: ToolCallingTurnResult, observation: str | None
) -> TurnRoutingInput:
    return TurnRoutingInput(
        action_handled=action_result.handled,
        executed_success_count=action_result.executed_success_count,
        has_observation=observation is not None,
        investigation_dispatched=action_result.investigation_dispatched,
    )


def _is_prior_investigation_follow_up_handoff(handoff_contents: tuple[str, ...]) -> bool:
    """True when the action planner handed off a session-prior-investigation follow-up."""
    return is_prior_investigation_follow_up(handoff_contents)


@dataclass(frozen=True)
class _RouteOutcome:
    """Effects of one routed path, ready to pack into ``TurnResult``."""

    final_intent: str
    response_text: str
    llm_run: Any | None = None


def _gather_and_answer(
    *,
    text: str,
    answer: StreamAnswerFn,
    gather: EvidenceGatherer,
    handoff_contents: tuple[str, ...],
    turn_plan: TurnPlan,
) -> Any | None:
    # Retrospective follow-ups already have grounding in ``last_state`` (injected
    # into the assistant prompt). Running the live gather loop for those turns
    # is wasteful and often violates "do not call integration tools" contracts
    # by probing Datadog/Sentry for a question the prior RCA already answered.
    # Not age-gated: the planner emitting the tag *is* the judgement that the
    # user means that incident, so a clock must not override it and answer with
    # current conditions instead of what happened.
    skip_gather = _is_prior_investigation_follow_up_handoff(handoff_contents) and (
        turn_plan.snapshot.last_state is not None
    )
    gathered = None if skip_gather else gather(text, turn_plan=turn_plan)
    if skip_gather:
        log.debug("gather skipped: follow_up handoff with prior investigation state")

    # Off-screen when we have evidence text so the prompt builder injects it;
    # on-screen (plain path) when there is nothing to inject.
    observation = gathered if gathered else None
    return answer(
        text,
        AnswerRequest(
            tool_observation=observation,
            tool_observation_on_screen=observation is None,
            handoff_contents=handoff_contents,
            turn_plan=turn_plan,
        ),
    )


def run_turn(
    text: str,
    session: SessionStore,
    *,
    execute_actions: ExecuteActions,
    answer: StreamAnswerFn,
    gather: EvidenceGatherer,
    accounting: TurnAccounting,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    surface: str = "interactive_shell",
) -> TurnResult:
    """Run one full turn through three paths, in order:

    1. ``summarize_observation`` — a successful action left discovery output, so
       summarize it into a direct answer.
    2. ``handled_without_llm`` — the action fully handled the turn; stop without the LLM.
    3. ``gather_and_answer`` — nothing was handled; gather evidence and answer.

    The path choice is the pure ``_route_turn``; this function performs the
    chosen path's effects. ``execute_actions``, ``answer``, and ``gather`` are
    already bound to the surface (session/output/tools) by the caller.

    ``surface`` selects the prompt :class:`~core.agent_harness.prompts.kernel.surfaces.SurfaceProfile`
    (e.g. gateway omits setup-state facts).
    """
    # Compact the session's conversation history before the turn if it has
    # grown past the threshold. Runs unconditionally: `auto_compact_if_needed`
    # is a no-op when compaction isn't required. Belongs at the harness layer
    # so every surface (shell, headless, gateway) benefits without re-implementing.
    auto_compact_if_needed(session)

    # Bare "yes"/"sure" after a Want me to: offer must resolve to that offer —
    # otherwise gateway Slack follow-ups hand off as brand-new vague requests.
    # Schedule offers prefer session.pending_schedule_offer (structured) over
    # scraping prose.
    prior_messages = getattr(session, "cli_agent_messages", None) or ()
    pending_schedule = getattr(session, "pending_schedule_offer", None)
    expanded = expand_affirmative_follow_up(
        text,
        prior_messages,
        pending_schedule=pending_schedule,
    )
    # Whether this turn is the confirmation of a pending offer. The offer is
    # consumed only once the command actually lands — clearing it here would
    # burn it on a rejected ``cron add``, leaving a second "yes" with nothing
    # to expand.
    confirms_schedule = (
        pending_schedule is not None
        and expanded.startswith("/cron ")
        and hasattr(session, "pending_schedule_offer")
    )
    text = expanded

    # Snapshot session state before any turn mutations. Both the action agent
    # and the conversational assistant read from this frozen context so their
    # prompts reflect a consistent turn-start view rather than live session state.
    turn_snapshot = TurnSnapshot.from_session(text, session, surface=surface)

    # Assemble the turn plan once: it resolves integrations and composes the
    # snapshot into the single object the action, gather, and answer phases read,
    # so they cannot disagree about what this turn knows.
    turn_plan = build_turn_plan(turn_snapshot, session)
    turn_snapshot = turn_plan.snapshot

    # Clear any observation left by a prior turn so only this turn's discovery
    # output can trigger a summary pass.
    session.last_command_observation = None
    agent_turn_executed_slashes(session).clear()

    action_result = execute_actions(
        text,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        turn_plan=turn_plan,
    )

    if (
        confirms_schedule
        and action_result.executed_success_count > 0
        and hasattr(session, "pending_schedule_offer")
    ):
        session.pending_schedule_offer = None
    accounting.record_action_result(action_result)

    handoff_contents = action_result.handoff_contents
    observation = session.last_command_observation
    route = _route_turn(
        _routing_input_from_result(action_result, observation),
        user_text=text,
        handoff_contents=handoff_contents,
    )
    log.debug(
        "turn route=%s planned=%s executed=%s handled=%s observation=%s",
        route.intent,
        action_result.planned_count,
        action_result.executed_count,
        action_result.handled,
        bool(observation),
    )
    sid = getattr(session, "session_id", None)
    emit_route(
        route.intent,
        session_id=sid,
        attributes={
            "planned_count": action_result.planned_count,
            "executed_count": action_result.executed_count,
            "handled": action_result.handled,
            "has_observation": bool(observation),
        },
    )

    with component_span(f"route:{route.intent}", session_id=sid):
        # if/elif + raise (not match/assert_never): CodeQL does not model
        # ``assert_never`` as noreturn, so locals bound only inside match arms
        # and read after the match trip py/uninitialized-local-variable.
        if route.intent == "summarize_observation":
            with apply_reasoning_effort(turn_snapshot.reasoning_effort):
                run = answer(
                    text,
                    AnswerRequest(
                        tool_observation=observation,
                        handoff_contents=handoff_contents,
                        turn_plan=turn_plan,
                    ),
                )
            outcome = _RouteOutcome(
                final_intent="cli_agent_summarized",
                response_text=_response_text(run),
                llm_run=run,
            )
        elif route.intent == "handled_without_llm":
            # The one route that never calls the model: the action already
            # produced the text the user sees.
            _record_action_only_turn(session, text, action_result.response_text)
            outcome = _RouteOutcome(
                final_intent="cli_agent_handled",
                response_text=action_result.response_text,
            )
        elif route.intent == "gather_and_answer":
            with apply_reasoning_effort(turn_snapshot.reasoning_effort):
                run = _gather_and_answer(
                    text=text,
                    answer=answer,
                    gather=gather,
                    handoff_contents=handoff_contents,
                    turn_plan=turn_plan,
                )
            outcome = _RouteOutcome(
                final_intent="cli_agent_fallback",
                response_text=_response_text(run),
                llm_run=run,
            )
        else:
            raise AssertionError(f"Unknown route intent: {route.intent!r}")

        return accounting.finalize(
            TurnResult(
                final_intent=outcome.final_intent,
                action_result=action_result,
                assistant_response_text=outcome.response_text,
                llm_run=outcome.llm_run,
            )
        )


__all__ = [
    "run_turn",
    "stream_answer",
]
