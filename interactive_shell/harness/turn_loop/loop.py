"""Shell-local agent loop for one interactive-shell turn."""

from __future__ import annotations

from config.llm_reasoning_effort import apply_reasoning_effort

from .types import ShellObservation, ShellTurnContext, ShellTurnDeps, ShellTurnResult


def run_shell_turn(context: ShellTurnContext, deps: ShellTurnDeps) -> ShellTurnResult:
    """Run one interactive-shell turn through action, observe, gather, and answer phases."""
    result = _run_phases(context, deps)
    hooks = deps.hooks
    if hooks is not None and hooks.on_turn_complete is not None:
        hooks.on_turn_complete(result)
    return result


def _run_phases(context: ShellTurnContext, deps: ShellTurnDeps) -> ShellTurnResult:
    session = context.session
    text = context.text
    console = context.console
    hooks = deps.hooks

    # Clear any observation left by a prior turn so only this turn's discovery
    # output can trigger a summary pass.
    session.last_command_observation = None

    turn = deps.execute_actions(
        text,
        session,
        console,
        confirm_fn=context.confirm_fn,
        is_tty=context.is_tty,
    )
    if hooks is not None and hooks.on_action_result is not None:
        hooks.on_action_result(turn)

    fallback_to_llm = not turn.handled
    snapshot = session.record_terminal_turn(
        executed_count=turn.executed_count,
        executed_success_count=turn.executed_success_count,
        fallback_to_llm=fallback_to_llm,
    )
    if deps.capture_terminal_turn is not None:
        deps.capture_terminal_turn(
            planned_count=turn.planned_count,
            executed_count=turn.executed_count,
            executed_success_count=turn.executed_success_count,
            fallback_to_llm=fallback_to_llm,
            session_turn_index=snapshot.turn_index,
            session_fallback_count=snapshot.fallback_count,
            session_action_success_percent=snapshot.action_success_percent,
            session_fallback_rate_percent=snapshot.fallback_rate_percent,
        )

    observations: list[ShellObservation] = []
    command_observation = session.last_command_observation
    if command_observation:
        observations.append(
            ShellObservation(
                source="terminal_action",
                text=command_observation,
                on_screen=True,
            )
        )

    if turn.handled and (turn.has_unhandled_clause or turn.executed_count > 0):
        if (
            command_observation
            and not turn.has_unhandled_clause
            and turn.executed_success_count > 0
        ):
            with apply_reasoning_effort(session.reasoning_effort):
                run = deps.answer_agent(
                    text,
                    session,
                    console,
                    confirm_fn=context.confirm_fn,
                    is_tty=context.is_tty,
                    tool_observation=command_observation,
                )
            if hooks is not None and hooks.on_llm_result is not None:
                hooks.on_llm_result(run)
            assistant_text = run.response_text if run is not None and run.response_text else ""
            if context.recorder is not None:
                context.recorder.set_response(assistant_text, run)
                context.recorder.flush()
            session.record("cli_agent", text)
            final_intent = "cli_agent_summarized"
            session.last_assistant_intent = final_intent
            return ShellTurnResult(
                final_intent=final_intent,
                action_result=turn,
                observations=tuple(observations),
                assistant_response_text=assistant_text,
                answered=True,
                llm_run=run,
            )

        final_intent = "cli_agent_denied" if turn.has_unhandled_clause else "cli_agent_handled"
        if context.recorder is not None:
            context.recorder.set_response(turn.response_text)
            context.recorder.flush()
        session.last_assistant_intent = final_intent
        return ShellTurnResult(
            final_intent=final_intent,
            action_result=turn,
            observations=tuple(observations),
            assistant_response_text=turn.response_text,
            answered=False,
        )

    with apply_reasoning_effort(session.reasoning_effort):
        gathered = deps.gather_evidence(text, session, console, is_tty=context.is_tty)
        if hooks is not None and hooks.on_gather_result is not None:
            hooks.on_gather_result(gathered)
        if gathered:
            observations.append(ShellObservation(source="gather", text=gathered, on_screen=False))
            run = deps.answer_agent(
                text,
                session,
                console,
                confirm_fn=context.confirm_fn,
                is_tty=context.is_tty,
                tool_observation=gathered,
                tool_observation_on_screen=False,
            )
        else:
            run = deps.answer_agent(
                text,
                session,
                console,
                confirm_fn=context.confirm_fn,
                is_tty=context.is_tty,
                tool_observation=None,
            )
    if hooks is not None and hooks.on_llm_result is not None:
        hooks.on_llm_result(run)

    assistant_text = run.response_text if run is not None and run.response_text else ""
    if context.recorder is not None:
        context.recorder.set_response(assistant_text, run)
        context.recorder.flush()
    session.record("cli_agent", text)
    final_intent = "cli_agent_handoff" if turn.handled else "cli_agent_fallback"
    session.last_assistant_intent = final_intent
    return ShellTurnResult(
        final_intent=final_intent,
        action_result=turn,
        observations=tuple(observations),
        assistant_response_text=assistant_text,
        answered=True,
        llm_run=run,
    )


__all__ = ["run_shell_turn"]
