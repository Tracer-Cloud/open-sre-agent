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

import time
from dataclasses import dataclass
from typing import Any, Literal

from config.llm_reasoning_effort import apply_reasoning_effort
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.ports import (
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
from core.agent_harness.prompts import build_cli_agent_prompt_from_provider
from core.agent_harness.prompts.conversation_memory import MAX_CONVERSATION_MESSAGES
from core.agent_harness.session.terminal_access import agent_turn_executed_slashes
from core.agent_harness.turns.transcript_compaction import auto_compact_if_needed
from core.agent_harness.turns.turn_plan import TurnPlan, build_turn_plan
from core.llm_invoke_errors import is_cli_timeout_error

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


def _stream_response(
    *,
    client: Any,
    prompt: str,
    output: OutputSink,
    run_factory: RunRecordFactory,
    error_reporter: ErrorReporter | None,
    session: Any | None = None,
) -> Any | None:
    try:
        started = time.monotonic()
        text_str = output.stream(
            label=_ASSISTANT_LABEL,
            chunks=client.invoke_stream(prompt),
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
            kind = "timeout" if is_cli_timeout_error(exc) else "assistant_error"
            stage_turn_error(session, kind, str(exc))
        output.render_error(f"assistant failed: {exc}")
        return None
    return run_factory.build(client=client, prompt=prompt, response_text=text_str, started=started)


def _record_answer_turn(session: SessionStore, message: str, assistant_text: str) -> None:
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", assistant_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]


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
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    handoff_contents: tuple[str, ...] = (),
    turn_plan: TurnPlan | None = None,
) -> Any | None:
    """Stream one grounded conversational answer (guidance only, no tools).

    The **direct answer** path (no tools): a single ``invoke_stream`` call with
    no ReAct loop. The **tool-calling** agent is ``core.agent.Agent`` — see
    ``core/agent_harness/AGENTS.md``.

    ``turn_plan`` is the turn-wide assembly built at turn start. Its snapshot
    (conversation history, integration state, prior investigation, synthetic-run
    path) grounds prompt construction in a stable turn-start view rather than the
    live session.
    """
    client = reasoning.get()
    if client is None:
        return None

    ctx = (
        turn_plan.snapshot if turn_plan is not None else TurnSnapshot.from_session(message, session)
    )
    _ = (confirm_fn, is_tty)

    prompt = build_cli_agent_prompt_from_provider(
        message=message,
        prompts=prompts,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        handoff_contents=handoff_contents,
        turn_snapshot=ctx,
    )

    run = _stream_response(
        client=client,
        prompt=prompt,
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
    )


def _gather_and_answer(
    *,
    text: str,
    answer: StreamAnswerFn,
    gather: EvidenceGatherer,
    confirm_fn: ConfirmFn | None,
    is_tty: bool | None,
    handoff_contents: tuple[str, ...],
    turn_plan: TurnPlan,
) -> Any | None:
    gathered = gather(text, is_tty=is_tty, turn_plan=turn_plan)

    # When evidence was gathered, mark it off-screen so the prompt builder
    # includes it. When nothing was gathered, omit the flag entirely so the
    # call shape matches the plain conversational (no-observation) path.
    on_screen: dict[str, bool] = {"tool_observation_on_screen": False} if gathered else {}

    return answer(
        text,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=gathered or None,
        handoff_contents=handoff_contents,
        turn_plan=turn_plan,
        **on_screen,
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
) -> ShellTurnResult:
    """Run one full turn through three paths, in order:

    1. ``summarize_observation`` — a successful action left discovery output, so
       summarize it into a direct answer.
    2. ``handled_without_llm`` — the action fully handled the turn; stop without the LLM.
    3. ``gather_and_answer`` — nothing was handled; gather evidence and answer.

    The path choice is the pure ``_route_turn``; this function performs the
    chosen path's effects. ``execute_actions``, ``answer``, and ``gather`` are
    already bound to the surface (session/output/tools) by the caller.
    """
    # Compact the session's conversation history before the turn if it has
    # grown past the threshold. Runs unconditionally: `auto_compact_if_needed`
    # is a no-op when compaction isn't required. Belongs at the harness layer
    # so every surface (shell, headless, gateway) benefits without re-implementing.
    auto_compact_if_needed(session)

    # Snapshot session state before any turn mutations. Both the action agent
    # and the conversational assistant read from this frozen context so their
    # prompts reflect a consistent turn-start view rather than live session state.
    turn_snapshot = TurnSnapshot.from_session(text, session)

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
    accounting.record_action_result(action_result)

    handoff_contents = action_result.handoff_contents
    observation = session.last_command_observation
    route = _route_turn(
        _routing_input_from_result(action_result, observation),
        user_text=text,
        handoff_contents=handoff_contents,
    )

    if route.intent == "summarize_observation":
        with apply_reasoning_effort(turn_snapshot.reasoning_effort):
            run = answer(
                text,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                tool_observation=observation,
                handoff_contents=handoff_contents,
                turn_plan=turn_plan,
            )
        result = ShellTurnResult(
            final_intent="cli_agent_summarized",
            action_result=action_result,
            assistant_response_text=_response_text(run),
            llm_run=run,
        )
    elif route.intent == "handled_without_llm":
        _record_action_only_turn(session, text, action_result.response_text)
        result = ShellTurnResult(
            final_intent="cli_agent_handled",
            action_result=action_result,
            assistant_response_text=action_result.response_text,
        )
    elif route.intent == "gather_and_answer":
        with apply_reasoning_effort(turn_snapshot.reasoning_effort):
            run = _gather_and_answer(
                text=text,
                answer=answer,
                gather=gather,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                handoff_contents=handoff_contents,
                turn_plan=turn_plan,
            )
        result = ShellTurnResult(
            final_intent="cli_agent_fallback",
            action_result=action_result,
            assistant_response_text=_response_text(run),
            llm_run=run,
        )
    else:
        raise AssertionError(f"Unknown route intent: {route.intent!r}")

    return accounting.finalize(result)


__all__ = [
    "run_turn",
    "stream_answer",
]
