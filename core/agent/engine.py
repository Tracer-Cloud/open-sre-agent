"""Decoupled turn engine: three-path routing + conversational assistant.

This is the surface-agnostic heart of the turn harness, lifted out of the
interactive shell. It owns:

* ``answer_cli_agent`` — one turn of the grounded conversational assistant
  (guidance only; no investigation run), streaming a reply, parsing an optional
  action plan, and recording the exchange.
* ``run_turn`` — the three-path routing (summarize-observation / handled /
  gather+answer) that sequences the action driver, the gather pass, and the
  assistant.

All terminal/session/grounding/telemetry concerns are reached through the
Protocols in :mod:`core.agent.ports`. Nothing here imports ``interactive_shell``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from config.llm_reasoning_effort import apply_reasoning_effort
from core.agent.action_plan import parse_action_plan
from core.agent.context import TurnContext
from core.agent.conversation_history import MAX_CONVERSATION_MESSAGES, format_recent_conversation
from core.agent.ports import (
    ActionDispatch,
    AnswerAgent,
    ConfirmFn,
    ErrorReporter,
    EvidenceGatherer,
    ExecuteActions,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    SessionStore,
    TurnAccounting,
)
from core.agent.prompts import _build_observation_block, _build_system_prompt
from core.agent.results import ShellTurnResult, ToolCallingTurnResult
from integrations.llm_cli.errors import CLITimeoutError

_logger = logging.getLogger(__name__)

_ASSISTANT_LABEL = "assistant"
_MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS = 120_000


# ---------------------------------------------------------------------------
# Grounding helpers (pure over the turn snapshot)
# ---------------------------------------------------------------------------


def _summarize_evidence(evidence: Any) -> list[str]:
    """Render a short evidence preview for the prior-investigation grounding block."""
    if isinstance(evidence, dict):
        sample_keys = list(evidence)[:3]
        sample = {key: evidence[key] for key in sample_keys}
        return [
            f"Evidence items: {len(evidence)}",
            "Evidence keys: " + ", ".join(map(str, sample_keys)),
            "Sample evidence:\n" + json.dumps(sample, indent=2, default=str)[:1500],
        ]
    if isinstance(evidence, list):
        return [
            f"Evidence items: {len(evidence)}",
            "Sample evidence:\n" + json.dumps(evidence[:3], indent=2, default=str)[:1500],
        ]
    return [
        f"Evidence type: {type(evidence).__name__}",
        f"Evidence summary:\n{str(evidence)[:1500]}",
    ]


def _summarize_last_state(state: dict[str, Any]) -> str:
    """Produce a compact text summary of the previous investigation for grounding."""
    parts: list[str] = []
    alert_name = state.get("alert_name")
    if alert_name:
        parts.append(f"Alert: {alert_name}")
    root_cause = state.get("root_cause")
    if root_cause:
        parts.append(f"Root cause: {root_cause}")
    problem_md = state.get("problem_md") or ""
    if problem_md:
        parts.append(f"Problem summary:\n{problem_md[:2000]}")
    slack_message = state.get("slack_message") or ""
    if slack_message:
        parts.append(f"Report:\n{slack_message[:2000]}")
    evidence = state.get("evidence")
    if evidence:
        try:
            parts.extend(_summarize_evidence(evidence))
        except (TypeError, ValueError) as exc:
            _logger.warning("could not serialize evidence for grounding: %s", exc)
            parts.append("(evidence present but could not be serialized for grounding)")
    return "\n\n".join(parts) or "(no prior investigation details available)"


def _user_message_requests_synthetic_failure_explanation(
    message: str, suggested_prompt: str
) -> bool:
    """True when the user is likely asking about a failed synthetic benchmark."""
    m = message.strip().lower()
    if not m:
        return False
    suggested = suggested_prompt.lower().rstrip("?")
    if suggested and m.rstrip("?") == suggested:
        return True
    if "why" in m and "fail" in m:
        return True
    return "what went wrong" in m


def _load_synthetic_observation_text(
    path_str: str, *, max_chars: int = _MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS
) -> str:
    try:
        raw = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(raw) > max_chars:
        return (
            raw[:max_chars]
            + f"\n… [truncated for prompt size; observation is {len(raw)} characters total]"
        )
    return raw


def _build_integration_guard(ctx: TurnContext) -> str:
    """Render the no-integrations guidance block (pure over the snapshot)."""
    if not (ctx.configured_integrations_known and not ctx.configured_integrations):
        return ""
    return (
        "No integrations are configured in this session. You may still help the user "
        "configure one: when they ask to set up, connect, or add an integration, emit a "
        "run_interactive action for `/integrations setup <service>` (or `/mcp connect "
        "<server>`). Do NOT emit run_cli_command or slash actions to show/verify/remove "
        "integrations that are not configured; for those, answer with guidance only.\n\n"
    )


def _build_synthetic_failure_block(ctx: TurnContext, suggested_prompt: str) -> str:
    obs_path = ctx.last_synthetic_observation_path
    if not obs_path:
        return ""
    if not _user_message_requests_synthetic_failure_explanation(ctx.text, suggested_prompt):
        return ""
    obs_text = _load_synthetic_observation_text(obs_path)
    if not obs_text:
        return ""
    return (
        "The user is asking about a failed `opensre tests synthetic` run "
        "in this checkout. The JSON below is the saved observation "
        f"(scores, gates, stderr summary). Path: {obs_path}\n"
        "Use it to explain validation failures. Do not say nothing ran or "
        "that you lack context — the run completed and this file was written.\n\n"
        f"--- observation_json ---\n{obs_text}\n\n"
    )


# ---------------------------------------------------------------------------
# Conversational assistant prompt (pure render + provider-backed collector)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliAgentPromptContext:
    """All string inputs needed to render the CLI-agent prompt, frozen."""

    reference: str
    agents_md: str
    investigation_flow: str
    history: str
    prior_investigation: str
    environment: str
    integration_guard: str
    observation_block: str
    synthetic_block: str
    user_message: str


def _render_cli_agent_prompt(ctx: CliAgentPromptContext) -> str:
    """Render the final prompt string from collected context (pure)."""
    system = _build_system_prompt(
        ctx.reference,
        ctx.history,
        agents_md=ctx.agents_md,
        investigation_flow=ctx.investigation_flow,
        prior_investigation=ctx.prior_investigation,
        environment=ctx.environment,
    )
    return (
        f"{system}\n"
        f"{ctx.integration_guard}"
        f"{ctx.observation_block}"
        f"{ctx.synthetic_block}"
        f"--- User message ---\n{ctx.user_message}"
    )


def _collect_cli_agent_prompt_context(
    *,
    message: str,
    prompts: PromptContextProvider,
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    turn_ctx: TurnContext,
) -> CliAgentPromptContext:
    """Read grounding sources / files / snapshot once into prompt context."""
    prompts.log_diagnostics("cli_agent_grounding")
    return CliAgentPromptContext(
        reference=prompts.cli_reference(),
        agents_md=prompts.agents_md(),
        investigation_flow=prompts.investigation_flow(),
        history=format_recent_conversation(list(turn_ctx.conversation_messages)),
        prior_investigation=(
            _summarize_last_state(turn_ctx.last_state) if turn_ctx.last_state is not None else ""
        ),
        environment=prompts.environment_block(),
        integration_guard=_build_integration_guard(turn_ctx),
        observation_block=_build_observation_block(
            tool_observation, on_screen=tool_observation_on_screen
        ),
        synthetic_block=_build_synthetic_failure_block(
            turn_ctx, prompts.suggested_synthetic_prompt()
        ),
        user_message=message,
    )


# ---------------------------------------------------------------------------
# Conversational assistant answer (interpreter edge for one turn)
# ---------------------------------------------------------------------------


def _stream_cli_agent_response(
    *,
    client: Any,
    prompt: str,
    output: OutputSink,
    run_factory: RunRecordFactory,
    error_reporter: ErrorReporter | None,
) -> Any | None:
    try:
        started = time.monotonic()
        text_str = output.stream(
            label=_ASSISTANT_LABEL,
            chunks=client.invoke_stream(prompt),
            suppress_if_starts_with="{",
        )
    except KeyboardInterrupt:
        output.print("· cancelled")
        return None
    except Exception as exc:
        if error_reporter is not None:
            error_reporter.report(
                exc,
                context="core.agent.engine.stream",
                expected=isinstance(exc, CLITimeoutError),
            )
        output.render_error(f"assistant failed: {exc}")
        return None
    return run_factory.build(client=client, prompt=prompt, response_text=text_str, started=started)


def _render_json_like_response(output: OutputSink, text: str) -> None:
    if not text.lstrip().startswith("{") or not text.strip():
        return
    output.render_markdown(text)


def _record_cli_agent_turn(session: SessionStore, message: str, assistant_text: str) -> None:
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", assistant_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]


def answer_cli_agent(
    message: str,
    session: SessionStore,
    output: OutputSink,
    *,
    prompts: PromptContextProvider,
    reasoning: ReasoningClientProvider,
    run_factory: RunRecordFactory,
    dispatch: ActionDispatch,
    error_reporter: ErrorReporter | None = None,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    turn_ctx: TurnContext | None = None,
) -> Any | None:
    """Run one turn of the conversational assistant (guidance only).

    ``turn_ctx`` is the immutable per-turn snapshot assembled at turn start.
    When present, snapshot fields (conversation history, integration state,
    prior investigation, synthetic-run path) are read from it rather than from
    the live session, so prompt construction reflects a stable turn-start view.
    """
    client = reasoning.get()
    if client is None:
        return None

    ctx = turn_ctx or TurnContext.from_session(message, session)

    prompt = _render_cli_agent_prompt(
        _collect_cli_agent_prompt_context(
            message=message,
            prompts=prompts,
            tool_observation=tool_observation,
            tool_observation_on_screen=tool_observation_on_screen,
            turn_ctx=ctx,
        )
    )

    run = _stream_cli_agent_response(
        client=client,
        prompt=prompt,
        output=output,
        run_factory=run_factory,
        error_reporter=error_reporter,
    )
    if run is None:
        return None

    text_str = getattr(run, "response_text", "") or ""
    handled = dispatch.execute(
        parse_action_plan(text_str),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )

    _record_cli_agent_turn(session, message, text_str)

    if not handled:
        _render_json_like_response(output, text_str)

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


def _route_turn(routing: TurnRoutingInput) -> TurnRoute:
    """Decide the turn path from routing facts (pure)."""
    if routing.action_handled and routing.has_observation and routing.executed_success_count > 0:
        return TurnRoute(intent="summarize_observation")
    if routing.action_handled:
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
    answer: AnswerAgent,
    gather: EvidenceGatherer,
    confirm_fn: ConfirmFn | None,
    is_tty: bool | None,
    turn_ctx: TurnContext,
) -> Any | None:
    gathered = gather(text, is_tty=is_tty)

    # When evidence was gathered, mark it off-screen so the prompt builder
    # includes it. When nothing was gathered, omit the flag entirely so the
    # call shape matches the plain conversational (no-observation) path.
    on_screen: dict[str, bool] = {"tool_observation_on_screen": False} if gathered else {}

    return answer(
        text,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=gathered or None,
        turn_ctx=turn_ctx,
        **on_screen,
    )


def run_turn(
    text: str,
    session: SessionStore,
    *,
    execute_actions: ExecuteActions,
    answer: AnswerAgent,
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
    # Snapshot session state before any turn mutations. Both the action agent
    # and the conversational assistant read from this frozen context so their
    # prompts reflect a consistent turn-start view rather than live session state.
    turn_ctx = TurnContext.from_session(text, session)

    # Clear any observation left by a prior turn so only this turn's discovery
    # output can trigger a summary pass.
    session.last_command_observation = None

    action_result = execute_actions(
        text,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        turn_ctx=turn_ctx,
    )
    accounting.record_action_result(action_result)

    observation = session.last_command_observation
    route = _route_turn(_routing_input_from_result(action_result, observation))

    match route.intent:
        case "summarize_observation":
            with apply_reasoning_effort(turn_ctx.reasoning_effort):
                run = answer(
                    text,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                    tool_observation=observation,
                    turn_ctx=turn_ctx,
                )
            result = ShellTurnResult(
                final_intent="cli_agent_summarized",
                action_result=action_result,
                assistant_response_text=_response_text(run),
                llm_run=run,
            )

        case "handled_without_llm":
            result = ShellTurnResult(
                final_intent="cli_agent_handled",
                action_result=action_result,
                assistant_response_text=action_result.response_text,
            )

        case "gather_and_answer":
            with apply_reasoning_effort(turn_ctx.reasoning_effort):
                run = _gather_and_answer(
                    text=text,
                    answer=answer,
                    gather=gather,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                    turn_ctx=turn_ctx,
                )
            result = ShellTurnResult(
                final_intent="cli_agent_fallback",
                action_result=action_result,
                assistant_response_text=_response_text(run),
                llm_run=run,
            )

    return accounting.finalize(result)


__all__ = [
    "CliAgentPromptContext",
    "answer_cli_agent",
    "run_turn",
]
