"""Interaction layer for the REPL execution policy.

This module owns the *user-facing* half of the execution gate: it renders the
policy decision (``Action blocked``, the non-TTY warning, the ``Proceed? [Y/n]``
prompt), reads the user's confirmation, and emits analytics. The pure decision
itself is computed by
:func:`tools.interactive_shell.shared.resolve_confirmation`,
which has no console, ``input``, or analytics dependency.

Keeping interaction here (rather than in ``execution_policy``) means the policy
module stays pure and easy to test, while callers that need the confirmation UX
import :func:`execution_allowed` from this UI module.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from core.agent_harness.session.terminal_access import trust_mode_enabled
from platform.analytics.cli import capture_repl_execution_policy_decision
from platform.analytics.provider import Properties
from platform.terminal.theme import DIM, WARNING
from tools.interactive_shell.shared import (
    ConfirmationOutcome,
    ExecutionPolicyResult,
    ExecutionVerdict,
    resolve_confirmation,
)

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime import Session


def _default_confirm_fn(prompt: str) -> str:
    return input(prompt)


DEFAULT_CONFIRM_FN: Callable[[str], str] = _default_confirm_fn


def _emit_decision(
    *,
    tool_type: str,
    policy_verdict: ExecutionVerdict,
    outcome: str,
    trust_mode: bool,
    reason: str | None,
    user_prompted: bool = False,
) -> None:
    props: Properties = {
        "tool_type": tool_type,
        "policy_verdict": policy_verdict,
        "outcome": outcome,
        "trust_mode": trust_mode,
    }
    if reason:
        props["reason"] = reason[:240]
    if user_prompted:
        props["user_prompted"] = True
    capture_repl_execution_policy_decision(props)


def execution_allowed(
    result: ExecutionPolicyResult,
    *,
    session: Session,
    console: Console,
    action_summary: str,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> bool:
    """Print policy UX, emit analytics, and return whether execution should proceed.

    When ``action_already_listed`` is True (e.g. assistant printed a numbered action plan),
    the TTY prompt omits repeating ``action_summary`` and shows only the policy reason.
    """
    trust_mode = trust_mode_enabled(session)
    tty = sys.stdin.isatty() if is_tty is None else is_tty
    confirm = confirm_fn or DEFAULT_CONFIRM_FN

    plan = resolve_confirmation(result, trust_mode=trust_mode, is_tty=tty)

    if plan.outcome == ConfirmationOutcome.DENY:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome=plan.analytics_outcome or "blocked",
            trust_mode=trust_mode,
            reason=plan.analytics_reason,
        )
        console.print(f"[{WARNING}]Action blocked:[/] {escape(result.reason or 'not allowed')}")
        if result.hint:
            console.print(f"[{DIM}]{escape(result.hint)}[/]")
        return False

    if plan.outcome == ConfirmationOutcome.ALLOW:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome=plan.analytics_outcome or "allowed",
            trust_mode=trust_mode,
            reason=plan.analytics_reason,
        )
        return True

    if plan.outcome == ConfirmationOutcome.BLOCK_NON_TTY:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome=plan.analytics_outcome or "blocked",
            trust_mode=trust_mode,
            reason=plan.analytics_reason,
        )
        console.print(
            f"[{WARNING}]confirmation required but stdin is not a TTY; "
            f"enable trust mode with[/] [bold]/trust[/bold] [{WARNING}]or rerun in a terminal.[/]"
        )
        console.print(f"[{DIM}]{escape(action_summary)}[/]")
        return False

    # NEEDS_CONFIRMATION
    reason = (result.reason or "this action").strip()
    summary = action_summary.strip()
    if action_already_listed:
        console.print(f"[{WARNING}]Confirm:[/] [{DIM}]{escape(reason)}[/]")
    elif summary:
        console.print(
            f"[{WARNING}]Confirm[/] [bold]{escape(summary)}[/bold] [{DIM}]— {escape(reason)}[/]"
        )
    else:
        console.print(f"[{WARNING}]Confirm:[/] [{DIM}]{escape(reason)}[/]")
    answer = confirm("Proceed? [Y/n] ").strip().lower()
    if answer not in {"", "y", "yes"}:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome="aborted",
            trust_mode=trust_mode,
            reason="user_declined",
            user_prompted=True,
        )
        console.print(f"[{DIM}]cancelled.[/]")
        return False

    _emit_decision(
        tool_type=result.tool_type,
        policy_verdict=result.verdict,
        outcome="allowed",
        trust_mode=trust_mode,
        reason="user_confirmed",
        user_prompted=True,
    )
    return True


__all__ = [
    "DEFAULT_CONFIRM_FN",
    "execution_allowed",
]
