"""Terminal action planning/execution for the interactive assistant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import PlannerLLMError
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
    plan_actions_with_llm_result,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_cli_actions,
    map_terminal_tasks,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
    ToolContext,
)
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, print_planned_actions
from app.cli.interactive_shell.ui.streaming import render_response_header

_DEFAULT_PLAN_ACTIONS_WITH_LLM = plan_actions_with_llm


@dataclass(frozen=True)
class TerminalActionExecutionResult:
    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool


@dataclass(frozen=True)
class ActionExecutionDeps:
    """Optional dependency seams used by tests/harnesses."""

    planner: Callable[..., Any] | None = None
    dispatch: Callable[..., bool] | None = None


@dataclass(frozen=True)
class _ActionPlanningDecision:
    actions: tuple[PlannedAction, ...]
    has_unhandled_clause: bool
    denied: bool
    policy_trace: tuple[str, ...]


def _coerce_action_plan_decision(
    raw: _ActionPlanningDecision
    | tuple[list[PlannedAction], bool]
    | tuple[list[PlannedAction], bool, bool],
) -> _ActionPlanningDecision:
    """Back-compat adapter for tests that monkeypatch _plan_actions to tuple output."""
    if isinstance(raw, _ActionPlanningDecision):
        return raw
    if len(raw) == 2:
        actions, has_unhandled_clause = raw
        denied = False
    else:
        actions, has_unhandled_clause, denied = raw
    return _ActionPlanningDecision(
        actions=tuple(actions),
        has_unhandled_clause=has_unhandled_clause,
        denied=denied,
        policy_trace=(),
    )


def _enforce_plan_fail_closed_policy(plan: _ActionPlanningDecision) -> _ActionPlanningDecision:
    if plan.denied:
        return plan
    actions = list(plan.actions)
    if not actions:
        return plan
    if all(action.kind == "assistant_handoff" for action in actions):
        if plan.has_unhandled_clause:
            return _ActionPlanningDecision((), True, True, plan.policy_trace)
        return _ActionPlanningDecision((), False, False, plan.policy_trace)
    if plan.has_unhandled_clause:
        return _ActionPlanningDecision((), True, True, plan.policy_trace)
    return _ActionPlanningDecision(tuple(actions), False, False, plan.policy_trace)


def _plan_actions(message: str, session: ReplSession) -> _ActionPlanningDecision:
    """Plan actions for a free-text message using LLM-first planning.

    Used to wrap the call in a ``rich.Live`` spinner for in-place
    "thinking…" feedback, but ``Live``'s cursor manipulation fights
    the now-always-active ``patch_stdout`` context that the persistent
    REPL holds for the lifetime of the session (produces transient
    cursor-jump / erase-line residue on every action-planning call).
    The bottom-toolbar spinner started by :func:`_run_one_dispatch`
    already animates throughout the dispatch — including this planning
    phase — so the user still sees feedback; no separate in-place
    indicator is needed here.
    """
    # Fast path: `!cmd` is an explicit shell-passthrough prefix that must bypass
    # the LLM planner entirely. The LLM misidentifies bare `!cmd` input (especially
    # multi-line `!cmd\n   args`) as a pasted snippet and returns assistant_handoff.
    stripped = message.strip()
    if stripped.startswith("!") and len(stripped) > 1:
        from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
            shell_action,
        )

        cmd = " ".join(stripped[1:].split())  # normalise internal whitespace/newlines
        if cmd:
            return _ActionPlanningDecision(
                actions=(shell_action(f"!{cmd}", 0),),
                has_unhandled_clause=False,
                denied=False,
                policy_trace=("deterministic_bang_shell",),
            )

    if plan_actions_with_llm is _DEFAULT_PLAN_ACTIONS_WITH_LLM:
        llm_plan_result = plan_actions_with_llm_result(message, session=session)
        if llm_plan_result is None:
            return _ActionPlanningDecision((), True, True, ("planner_unavailable",))
        actions = list(llm_plan_result.actions)
        has_unhandled_clause = llm_plan_result.has_unhandled_clause
        policy_trace = llm_plan_result.policy_trace
    else:
        # Preserve existing monkeypatch seam used by unit tests and debug harnesses.
        llm_plan_legacy = plan_actions_with_llm(message, session=session)
        if llm_plan_legacy is None:
            return _ActionPlanningDecision((), True, True, ("planner_unavailable",))
        actions, has_unhandled_clause = llm_plan_legacy
        policy_trace = ()
    if not actions:
        return _ActionPlanningDecision((), has_unhandled_clause, False, policy_trace)
    if all(action.kind == "assistant_handoff" for action in actions):
        # If the planner surfaced an assistant handoff *and* flagged unhandled
        # content, treat this as a fail-closed deny path. This handles partial
        # prompts where only some clauses were actionable.
        if has_unhandled_clause:
            return _ActionPlanningDecision((), True, True, policy_trace)
        # Pure handoff: let the caller invoke the LLM reply directly without
        # printing a noisy "Requested actions: assistant handoff …" header.
        return _ActionPlanningDecision((), False, False, policy_trace)
    if has_unhandled_clause:
        return _ActionPlanningDecision((), True, True, policy_trace)
    return _ActionPlanningDecision(tuple(actions), False, False, policy_trace)


def _render_plan_denied(console: Console) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(
        "[yellow]I couldn't safely decide actions for that request.[/] "
        "Please rephrase or use explicit slash commands."
    )


_CLI_AGENT_MSG_CAP = 24  # mirrors _MAX_CLI_AGENT_TURNS * 2 in cli_agent.py


def _render_planner_llm_error(console: Console, message: str) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(f"[yellow]{escape(message)}[/]")


def _persist_error_turn(session: ReplSession, user_text: str, error_text: str) -> None:
    """Record a failed assistant turn in cli_agent_messages so /resume can display it."""
    session.cli_agent_messages.append(("user", user_text))
    session.cli_agent_messages.append(("assistant", error_text))
    if len(session.cli_agent_messages) > _CLI_AGENT_MSG_CAP:
        session.cli_agent_messages[:] = session.cli_agent_messages[-_CLI_AGENT_MSG_CAP:]


def _tool_args_for_action(action: PlannedAction) -> dict[str, Any]:
    if action.args:
        return dict(action.args)
    content = action.content.strip()
    if action.kind == "slash":
        parts = content.split()
        return {
            "command": parts[0] if parts else "",
            "args": parts[1:] if len(parts) > 1 else [],
        }
    if action.kind == "llm_provider":
        return {"provider": content}
    if action.kind == "shell":
        return {"command": content}
    if action.kind == "sample_alert":
        return {"template": content}
    if action.kind == "investigation":
        return {"alert_text": content}
    if action.kind == "synthetic_test":
        suite, _sep, scenario = content.partition(":")
        return {"suite": suite, "scenario": scenario}
    if action.kind == "task_cancel":
        return {"target": content}
    if action.kind == "cli_command":
        return {"payload": content}
    if action.kind == "implementation":
        return {"task": content}
    return {"content": content}


def _execute_planned_actions(
    *,
    actions: list[PlannedAction],
    has_unhandled_clause: bool,
    message: str,
    session: ReplSession,
    console: Console,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    dispatch_fn: Callable[..., bool] | None = None,
) -> bool:
    console.print()
    render_response_header(console, "assistant")
    print_planned_actions(console, actions)
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        # Multi-action plans: if the user pressed Esc / typed
        # ``/cancel`` between actions, the per-dispatch cancel event
        # is set on the ``StreamingConsole``. Skip the rest of the
        # plan so a "run all of these" plan doesn't keep marching
        # through after an explicit cancel. ``getattr`` with a default
        # keeps non-streaming consoles (used by the seeded-input
        # test path) working unchanged.
        if getattr(console, "cancel_requested", False):
            console.print(f"[{DIM}](remaining actions cancelled)[/]")
            break
        console.print()
        tool_name = ACTION_KIND_TO_TOOL.get(action.kind)
        if tool_name is None:
            continue
        if dispatch_fn is None:
            REGISTRY.dispatch(
                tool_name=tool_name,
                args=_tool_args_for_action(action),
                ctx=ToolContext(
                    session=session,
                    console=console,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                    action_already_listed=True,
                ),
            )
        else:
            dispatch_fn(
                tool_name=tool_name,
                args=_tool_args_for_action(action),
                ctx=ToolContext(
                    session=session,
                    console=console,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                    action_already_listed=True,
                ),
            )

    console.print()
    return not has_unhandled_clause


def execute_cli_actions(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ActionExecutionDeps | None = None,
) -> bool:
    """Execute inferred actions from LLM-first planning.

    Returns True when the request was handled (including explicit fail-closed
    denials). Returns False only for legacy/test paths that pass through with no
    planned actions and no deny signal.
    """
    if deps is not None and deps.planner is not None:
        planned = deps.planner(message, session=session)
        plan = (
            _ActionPlanningDecision((), True, True, ("planner_unavailable",))
            if planned is None
            else _coerce_action_plan_decision(planned)
        )
    else:
        try:
            plan = _coerce_action_plan_decision(_plan_actions(message, session))
        except PlannerLLMError as exc:
            error_text = str(exc)
            _render_planner_llm_error(console, error_text)
            _persist_error_turn(session, message, error_text)
            session.record("cli_agent", message, ok=False)
            return True
    plan = _enforce_plan_fail_closed_policy(plan)
    actions = list(plan.actions)
    has_unhandled_clause = plan.has_unhandled_clause
    denied = plan.denied
    if denied:
        _render_plan_denied(console)
        session.record("cli_agent", message, ok=False)
        return True
    if not actions:
        return False
    return _execute_planned_actions(
        actions=actions,
        has_unhandled_clause=has_unhandled_clause,
        message=message,
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        dispatch_fn=deps.dispatch if deps is not None else None,
    )


def execute_cli_actions_with_metrics(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ActionExecutionDeps | None = None,
) -> TerminalActionExecutionResult:
    """Execute planned actions and return per-turn action counters.

    ``confirm_fn`` is forwarded to :func:`execute_cli_actions` so the
    interactive REPL can route mid-dispatch ``Proceed? [y/N]`` prompts
    through its active prompt_toolkit input instead of the stdlib
    ``input()`` (which deadlocks against the running ``prompt_async``).
    """
    from app.analytics.cli import (
        capture_repl_execution_policy_decision,
        capture_terminal_actions_executed,
        capture_terminal_actions_planned,
    )

    if deps is not None and deps.planner is not None:
        planned = deps.planner(message, session=session)
        plan = (
            _ActionPlanningDecision((), True, True, ("planner_unavailable",))
            if planned is None
            else _coerce_action_plan_decision(planned)
        )
    else:
        try:
            plan = _coerce_action_plan_decision(_plan_actions(message, session))
        except PlannerLLMError as exc:
            error_text = str(exc)
            _render_planner_llm_error(console, error_text)
            _persist_error_turn(session, message, error_text)
            session.record("cli_agent", message, ok=False)
            capture_terminal_actions_executed(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
            )
            return TerminalActionExecutionResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=True,
                handled=True,
            )
    plan = _enforce_plan_fail_closed_policy(plan)
    actions = list(plan.actions)
    has_unhandled_clause = plan.has_unhandled_clause
    denied = plan.denied
    capture_terminal_actions_planned(
        planned_count=len(actions),
        has_unhandled_clause=has_unhandled_clause,
    )
    capture_repl_execution_policy_decision(
        {
            "policy_stage": "terminal_action_planning",
            "policy_trace": ",".join(plan.policy_trace),
            "planned_count": len(actions),
            "has_unhandled_clause": has_unhandled_clause,
            "denied": denied,
        }
    )
    if denied:
        _render_plan_denied(console)
        session.record("cli_agent", message, ok=False)
        capture_terminal_actions_executed(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
        )
        return TerminalActionExecutionResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=True,
        )
    if not actions:
        return TerminalActionExecutionResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=has_unhandled_clause,
            handled=False,
        )

    history_start = len(session.history)
    handled = _execute_planned_actions(
        actions=actions,
        has_unhandled_clause=has_unhandled_clause,
        message=message,
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        dispatch_fn=deps.dispatch if deps is not None else None,
    )
    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type")
        in {"slash", "shell", "alert", "synthetic_test", "implementation", "cli_command"}
    ]
    executed_count = len(executed_entries)
    executed_success_count = sum(1 for item in executed_entries if item.get("ok", True))
    capture_terminal_actions_executed(
        planned_count=len(actions),
        executed_count=executed_count,
        executed_success_count=executed_success_count,
    )
    return TerminalActionExecutionResult(
        planned_count=len(actions),
        executed_count=executed_count,
        executed_success_count=executed_success_count,
        has_unhandled_clause=has_unhandled_clause,
        handled=handled,
    )


def plan_cli_actions(message: str) -> list[str]:
    """Backward-compatible alias for ``map_cli_actions``."""
    return map_cli_actions(message)


def plan_terminal_tasks(message: str) -> list[str]:
    """Backward-compatible alias for ``map_terminal_tasks``."""
    return map_terminal_tasks(message)


__all__ = [
    "ActionExecutionDeps",
    "TerminalActionExecutionResult",
    "execute_cli_actions",
    "execute_cli_actions_with_metrics",
    "map_cli_actions",
    "map_terminal_tasks",
    "plan_cli_actions",
    "plan_terminal_tasks",
]
