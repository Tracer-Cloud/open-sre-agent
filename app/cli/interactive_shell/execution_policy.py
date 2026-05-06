"""Central execution policy (allow / ask / deny) for interactive REPL actions."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.markup import escape

import app.cli.interactive_shell.intent_parser as _intent_parser
from app.analytics.cli import capture_repl_execution_policy_decision
from app.cli.interactive_shell.execution_tier import ExecutionTier
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.shell_policy import (
    ParsedShellCommand,
    evaluate_policy,
    parse_shell_command,
)

ExecutionVerdict = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class ExecutionPolicyResult:
    """Result of evaluating whether an action may run."""

    verdict: ExecutionVerdict
    action_type: str
    reason: str | None
    hint: str | None = None
    shell_classification: str | None = None


def _default_confirm_fn(prompt: str) -> str:
    return input(prompt)


DEFAULT_CONFIRM_FN: Callable[[str], str] = _default_confirm_fn


def resolve_slash_execution_tier(
    command_name: str, args: list[str], registered: ExecutionTier
) -> ExecutionTier:
    """Refine tier using subcommands where the registry defaults are too coarse."""
    if registered == ExecutionTier.EXEMPT:
        return ExecutionTier.EXEMPT
    key = command_name.lower()
    if key == "/model":
        sub = (args[0].lower() if args else "show").strip()
        if sub in {"show"}:
            return ExecutionTier.SAFE
        if sub == "toolcall":
            if len(args) >= 2 and args[1].lower() in {"set", "use", "switch"}:
                return ExecutionTier.ELEVATED
            return ExecutionTier.SAFE
        if sub in {"set", "use", "switch"}:
            return ExecutionTier.ELEVATED
        return ExecutionTier.SAFE
    if key == "/integrations":
        sub = (args[0].lower() if args else "list").strip()
        if sub == "verify":
            return ExecutionTier.ELEVATED
        return ExecutionTier.SAFE
    return registered


def _emit_decision(
    *,
    action_type: str,
    policy_verdict: ExecutionVerdict,
    outcome: str,
    trust_mode: bool,
    reason: str | None,
    user_prompted: bool = False,
) -> None:
    props: dict[str, str | bool] = {
        "action_type": action_type,
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
    session: ReplSession,
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
    trust_mode = session.trust_mode
    tty = sys.stdin.isatty() if is_tty is None else is_tty
    confirm = confirm_fn or DEFAULT_CONFIRM_FN

    if result.verdict == "deny":
        _emit_decision(
            action_type=result.action_type,
            policy_verdict="deny",
            outcome="blocked",
            trust_mode=trust_mode,
            reason=result.reason,
        )
        console.print(f"[yellow]Action blocked:[/yellow] {escape(result.reason or 'not allowed')}")
        if result.hint:
            console.print(f"[dim]{escape(result.hint)}[/dim]")
        return False

    if result.verdict == "allow":
        _emit_decision(
            action_type=result.action_type,
            policy_verdict="allow",
            outcome="allowed",
            trust_mode=trust_mode,
            reason=result.reason,
        )
        return True

    # ask
    if trust_mode:
        _emit_decision(
            action_type=result.action_type,
            policy_verdict="ask",
            outcome="allowed",
            trust_mode=trust_mode,
            reason="trust_mode_skipped_prompt",
        )
        return True

    if not tty:
        _emit_decision(
            action_type=result.action_type,
            policy_verdict="ask",
            outcome="blocked",
            trust_mode=trust_mode,
            reason="non_interactive_stdin",
        )
        console.print(
            "[yellow]confirmation required but stdin is not a TTY; "
            "enable trust mode with[/yellow] [bold]/trust[/bold] [yellow]or rerun in a terminal.[/yellow]"
        )
        console.print(f"[dim]{escape(action_summary)}[/dim]")
        return False

    reason = (result.reason or "this action").strip()
    summary = action_summary.strip()
    if action_already_listed:
        console.print(f"[yellow]Confirm:[/yellow] [dim]{escape(reason)}[/dim]")
    elif summary:
        console.print(
            f"[yellow]Confirm[/yellow] [bold]{escape(summary)}[/bold] [dim]— {escape(reason)}[/dim]"
        )
    else:
        console.print(f"[yellow]Confirm:[/yellow] [dim]{escape(reason)}[/dim]")
    answer = confirm("Proceed? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        _emit_decision(
            action_type=result.action_type,
            policy_verdict="ask",
            outcome="aborted",
            trust_mode=trust_mode,
            reason="user_declined",
            user_prompted=True,
        )
        console.print("[dim]cancelled.[/dim]")
        return False

    _emit_decision(
        action_type=result.action_type,
        policy_verdict="ask",
        outcome="allowed",
        trust_mode=trust_mode,
        reason="user_confirmed",
        user_prompted=True,
    )
    return True


def evaluate_shell_from_parsed(parsed: ParsedShellCommand) -> ExecutionPolicyResult:
    """Like :func:`evaluate_shell_command` but reuses an existing parse result."""
    d = evaluate_policy(parsed=parsed)

    if parsed.parse_error is not None:
        return ExecutionPolicyResult(
            verdict="deny",
            action_type="shell",
            reason=d.reason,
            hint=d.hint,
            shell_classification=d.classification,
        )

    if parsed.passthrough:
        return ExecutionPolicyResult(
            verdict="ask",
            action_type="shell",
            reason="explicit shell passthrough (!) runs your full user shell",
            hint=d.hint,
            shell_classification=d.classification,
        )

    if d.allow:
        return ExecutionPolicyResult(
            verdict="allow",
            action_type="shell",
            reason=None,
            shell_classification=d.classification,
        )

    if d.classification == "restricted":
        return ExecutionPolicyResult(
            verdict="deny",
            action_type="shell",
            reason=d.reason,
            hint=d.hint,
            shell_classification=d.classification,
        )

    if parsed.argv is None:
        return ExecutionPolicyResult(
            verdict="deny",
            action_type="shell",
            reason=d.reason or "failed to parse command.",
            hint=d.hint,
            shell_classification=d.classification,
        )

    return ExecutionPolicyResult(
        verdict="ask",
        action_type="shell",
        reason=d.reason,
        hint=d.hint,
        shell_classification=d.classification,
    )


def evaluate_shell_command(command: str) -> ExecutionPolicyResult:
    """Map shell policy + passthrough rules into allow/ask/deny."""
    parsed = parse_shell_command(command, is_windows=_intent_parser.IS_WINDOWS)
    return evaluate_shell_from_parsed(parsed)


def evaluate_slash_tier(tier: ExecutionTier) -> ExecutionPolicyResult:
    """Turn a resolved slash tier into an execution verdict."""
    if tier == ExecutionTier.EXEMPT or tier == ExecutionTier.SAFE:
        return ExecutionPolicyResult(verdict="allow", action_type="slash", reason=None)
    return ExecutionPolicyResult(
        verdict="ask",
        action_type="slash",
        reason="this command may change configuration or run heavy work",
    )


def evaluate_investigation_launch(
    *, action_type: Literal["investigation", "sample_alert"]
) -> ExecutionPolicyResult:
    """Policy for starting an RCA / LangGraph investigation from the REPL."""
    return ExecutionPolicyResult(
        verdict="ask",
        action_type=action_type,
        reason="investigations call external tools and consume LLM quota",
    )


def evaluate_synthetic_test_launch() -> ExecutionPolicyResult:
    return ExecutionPolicyResult(
        verdict="ask",
        action_type="synthetic_test",
        reason="synthetic tests spawn a long-running subprocess",
    )


def evaluate_llm_runtime_switch(*, action_type: str) -> ExecutionPolicyResult:
    return ExecutionPolicyResult(
        verdict="ask",
        action_type=action_type,
        reason="this updates LLM provider / model environment configuration",
    )


__all__ = [
    "DEFAULT_CONFIRM_FN",
    "ExecutionPolicyResult",
    "ExecutionVerdict",
    "evaluate_investigation_launch",
    "evaluate_llm_runtime_switch",
    "evaluate_shell_command",
    "evaluate_shell_from_parsed",
    "evaluate_slash_tier",
    "evaluate_synthetic_test_launch",
    "execution_allowed",
    "resolve_slash_execution_tier",
]
