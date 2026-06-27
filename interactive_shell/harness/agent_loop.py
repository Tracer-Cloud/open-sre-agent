"""Per-prompt shell agent loop.

This module owns the repeatable mechanics for one submitted shell prompt:
snapshot context, clear stale observations, run the action-agent pass, route the
result, optionally gather evidence, optionally call the assistant, and finalize
accounting. The durable lifecycle object lives in ``harness/agent.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from rich.console import Console

from config.llm_reasoning_effort import apply_reasoning_effort
from interactive_shell.harness.agent_context import AgentContext
from interactive_shell.harness.llm_context.session import ReplSession
from interactive_shell.harness.response import generate_response
from interactive_shell.harness.tool_calling import run_tool_calling_turn
from interactive_shell.runtime.core.turn_accounting import (
    ShellTurnAccounting,
    ShellTurnResult,
    ToolCallingTurnResult,
)
from interactive_shell.tools.tool_gathering import gather_tool_evidence
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder

RunToolCallingTurn = Callable[..., ToolCallingTurnResult]
GatherEvidence = Callable[..., str | None]
ResponseGenerator = Callable[..., LlmRunInfo | None]


def _response_text(run: LlmRunInfo | None) -> str:
    return run.response_text if run is not None and run.response_text else ""


def _route_prompt(
    action_result: ToolCallingTurnResult, observation: str | None
) -> Literal["summarize_observation", "handled_without_llm", "gather_and_answer"]:
    """Decide the prompt path from the action result and any left-over observation."""
    if (
        action_result.handled
        and observation is not None
        and action_result.executed_success_count > 0
    ):
        return "summarize_observation"
    if action_result.handled:
        return "handled_without_llm"
    return "gather_and_answer"


def _gather_and_answer(
    *,
    text: str,
    session: ReplSession,
    console: Console,
    gather_evidence: GatherEvidence,
    response_generator: ResponseGenerator,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    agent_ctx: AgentContext,
) -> LlmRunInfo | None:
    gathered = gather_evidence(text, session, console, is_tty=is_tty)

    # When evidence was gathered, mark it off-screen so the prompt builder
    # includes it. When nothing was gathered, omit the flag entirely so the
    # call shape matches the plain conversational (no-observation) path.
    on_screen: dict[str, bool] = {"tool_observation_on_screen": False} if gathered else {}

    return response_generator(
        text,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=gathered or None,
        agent_ctx=agent_ctx,
        **on_screen,
    )


def run_agent_prompt(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    execute_actions: RunToolCallingTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    response_generator: ResponseGenerator | None = None,
) -> ShellTurnResult:
    """Run one prompt through the shell agent's action/answer loop."""
    execute_actions = execute_actions or run_tool_calling_turn
    gather_evidence = gather_evidence or gather_tool_evidence
    response_generator = response_generator or generate_response

    # Snapshot session state before any prompt mutations. Both the action
    # agent and the conversational assistant read from this frozen context so
    # prompts reflect a consistent prompt-start view rather than live session
    # state.
    agent_ctx = AgentContext.from_session(text, session)
    accounting = ShellTurnAccounting(session=session, text=text, recorder=recorder)

    # Clear any observation left by a prior prompt so only this prompt's
    # discovery output can trigger a summary pass.
    session.agent.reset_observation()

    action_result = execute_actions(
        text,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        agent_ctx=agent_ctx,
    )
    accounting.record_action_result(action_result)

    observation = session.agent.last_observation

    route = _route_prompt(action_result, observation)
    if route == "summarize_observation":
        with apply_reasoning_effort(agent_ctx.reasoning_effort):
            run = response_generator(
                text,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                tool_observation=observation,
                agent_ctx=agent_ctx,
            )
        return accounting.finalize(
            ShellTurnResult(
                final_intent="cli_agent_summarized",
                action_result=action_result,
                assistant_response_text=_response_text(run),
                llm_run=run,
            )
        )

    if route == "handled_without_llm":
        return accounting.finalize(
            ShellTurnResult(
                final_intent="cli_agent_handled",
                action_result=action_result,
                assistant_response_text=action_result.response_text,
            )
        )

    if route == "gather_and_answer":
        with apply_reasoning_effort(agent_ctx.reasoning_effort):
            run = _gather_and_answer(
                text=text,
                session=session,
                console=console,
                gather_evidence=gather_evidence,
                response_generator=response_generator,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                agent_ctx=agent_ctx,
            )
        return accounting.finalize(
            ShellTurnResult(
                final_intent="cli_agent_fallback",
                action_result=action_result,
                assistant_response_text=_response_text(run),
                llm_run=run,
            )
        )

    raise AssertionError(f"Unhandled turn route: {route!r}")


__all__ = [
    "GatherEvidence",
    "ResponseGenerator",
    "RunToolCallingTurn",
    "run_agent_prompt",
]
