"""Facade for second-phase terminal action handling.

The interactive shell routes free text to the CLI-agent path first. Before the
assistant writes a conversational answer, this facade asks the second-phase
planner whether the user explicitly requested terminal work, then dispatches any
approved actions through the tool registry.

The implementation lives in the ``terminal_actions`` package:

- ``planning`` decides whether there is an executable plan.
- ``execution`` owns the public execution flow and per-turn metrics.
- ``dispatch`` converts planned actions into tool calls.
- ``feedback`` owns user-visible denial/error output.

Keep this file as the stable import surface for runtime and tests.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from cli.interactive_shell.runtime import ReplSession

from .terminal_actions.execution import (
    execute_cli_actions as _execute_cli_actions_impl,
)
from .terminal_actions.models import (
    ActionExecutionDeps,
    TerminalActionExecutionResult,
)
from .terminal_actions.models import (
    ActionPlanningDecision as _ActionPlanningDecision,
)
from .terminal_actions.planning import plan_actions as _plan_actions_impl

_DEFAULT_PLAN_ACTIONS_WITH_LLM = plan_actions_with_llm


def _plan_actions(message: str, session: ReplSession) -> _ActionPlanningDecision:
    return _plan_actions_impl(
        message,
        session,
        planner=plan_actions_with_llm,
        default_planner=_DEFAULT_PLAN_ACTIONS_WITH_LLM,
    )


def execute_cli_actions(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ActionExecutionDeps | None = None,
) -> TerminalActionExecutionResult:
    return _execute_cli_actions_impl(
        message,
        session,
        console,
        plan_actions_fn=_plan_actions,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        deps=deps,
    )


__all__ = [
    "ActionExecutionDeps",
    "TerminalActionExecutionResult",
    "execute_cli_actions",
]
