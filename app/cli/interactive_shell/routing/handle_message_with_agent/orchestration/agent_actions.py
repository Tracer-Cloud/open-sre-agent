"""Terminal action planning/execution for the interactive assistant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

# Load tool registrations.
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration import (  # noqa: F401
    tools,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
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


@dataclass(frozen=True)
class TerminalActionExecutionResult:
    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool


def _plan_actions(message: str, session: ReplSession) -> tuple[list[PlannedAction], bool, bool]:
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
            return [shell_action(f"!{cmd}", 0)], False, False

    llm_plan = plan_actions_with_llm(message, session=session)
    if llm_plan is None:
        return [], True, True
    actions, has_unhandled_clause = llm_plan
    if not actions:
        return [], has_unhandled_clause, False
    if all(action.kind == "assistant_handoff" for action in actions):
        # If the planner surfaced an assistant handoff *and* flagged unhandled
        # content, treat this as a fail-closed deny path. This handles partial
        # prompts where only some clauses were actionable.
        if has_unhandled_clause:
            return [], True, True
        # Pure handoff: let the caller invoke the LLM reply directly without
        # printing a noisy "Requested actions: assistant handoff …" header.
        return [], False, False
    if has_unhandled_clause:
        return [], True, True
    return actions, False, False


def _render_plan_denied(console: Console) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(
        "[yellow]I couldn't safely decide actions for that request.[/] "
        "Please rephrase or use explicit slash commands."
    )


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

    console.print()
    return not has_unhandled_clause


def execute_cli_actions(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Execute inferred actions from LLM-first planning.

    Returns True when the request was handled (including explicit fail-closed
    denials). Returns False only for legacy/test paths that pass through with no
    planned actions and no deny signal.
    """
    actions, has_unhandled_clause, denied = _plan_actions(message, session)
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
    )


def execute_cli_actions_with_metrics(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
) -> TerminalActionExecutionResult:
    """Execute planned actions and return per-turn action counters.

    ``confirm_fn`` is forwarded to :func:`execute_cli_actions` so the
    interactive REPL can route mid-dispatch ``Proceed? [y/N]`` prompts
    through its active prompt_toolkit input instead of the stdlib
    ``input()`` (which deadlocks against the running ``prompt_async``).
    """
    from app.analytics.cli import (
        capture_terminal_actions_executed,
        capture_terminal_actions_planned,
    )

    actions, has_unhandled_clause, denied = _plan_actions(message, session)
    capture_terminal_actions_planned(
        planned_count=len(actions),
        has_unhandled_clause=has_unhandled_clause,
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
    "TerminalActionExecutionResult",
    "execute_cli_actions",
    "execute_cli_actions_with_metrics",
    "map_cli_actions",
    "map_terminal_tasks",
    "plan_cli_actions",
    "plan_terminal_tasks",
]
