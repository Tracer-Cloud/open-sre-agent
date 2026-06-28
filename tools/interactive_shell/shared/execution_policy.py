"""Central execution policy (allow / ask / deny) for interactive REPL tools.

Alpha mode: allow everything
----------------------------
OpenSRE is in **alpha**, and the interactive REPL runs with **no command
guardrails** so developer velocity stays high. Every policy decision below
resolves to ``allow`` and nothing prompts for confirmation: slash/``opensre``
commands (any tier), investigations, synthetic tests, code-agent launches, LLM
runtime switches, and shell commands of every kind — read-only, mutating,
``restricted`` (``sudo``, ``systemctl``, ``kill``, ``dd`` …), shell operators
(``| && ; > <``), and command substitution (`` ` ``/``$(...)``) — all run
immediately, in any context (TTY or not, trust mode or not).

There is intentionally **no shell-command safety policy**: the former
read-only / mutating / restricted classification and its deny floor were removed
(see ``docs/interactive-shell-action-policy.md``). The only thing shell
evaluation still rejects is genuinely empty input (a bare ``!`` or whitespace),
which is input validation rather than a guardrail.

The ``ask`` verdict is retained so that ``trust_mode`` and any future opt-in
stricter policy still have a hook, but the policy functions here never emit
``ask``. If guardrails are reintroduced after alpha, gate them here at the
execution stage (not the planner).

This module is intentionally **pure**: it has no terminal I/O, no analytics, and
no console dependency. The decision is computed by :func:`resolve_confirmation`,
and the interaction layer (printing the reason/hint, the ``Proceed? [Y/n]``
prompt, and analytics emission) lives in
``interactive_shell.ui.execution_confirm.execution_allowed``.

Shell-specific evaluation (empty-input rejection, ``plan_shell_execution``)
lives next to the rest of the shell machinery in
``tools.interactive_shell.shell.policy`` and reuses the contracts defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

ExecutionVerdict = Literal["allow", "ask", "deny"]


class ToolExecutionMode(StrEnum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    FOREGROUND_STREAMING = "foreground_streaming"


@dataclass(frozen=True)
class ExecutionPolicyResult:
    """Result of evaluating whether a tool may run."""

    verdict: ExecutionVerdict
    tool_type: str
    reason: str | None
    hint: str | None = None
    shell_classification: str | None = None


@dataclass(frozen=True)
class ToolExecutionPlan:
    """Unified execution plan contract shared across tool executors."""

    tool_type: str
    classification: str
    execution_mode: ToolExecutionMode
    policy: ExecutionPolicyResult


class ConfirmationOutcome(StrEnum):
    """Pure decision for how the interaction layer should treat an action."""

    ALLOW = "allow"  # proceed, no prompt
    DENY = "deny"  # blocked by policy (show reason + hint)
    BLOCK_NON_TTY = "block_non_tty"  # ask verdict but stdin is not a TTY
    NEEDS_CONFIRMATION = "needs_confirmation"  # prompt the user


@dataclass(frozen=True)
class ConfirmationPlan:
    """Result of :func:`resolve_confirmation` (side-effect free).

    ``analytics_outcome`` / ``analytics_reason`` carry the values the interaction
    layer should emit for the non-prompt outcomes (ALLOW / DENY / BLOCK_NON_TTY).
    For ``NEEDS_CONFIRMATION`` the analytics outcome depends on the user's answer
    and is decided by the interaction layer, so both fields are ``None``.
    """

    outcome: ConfirmationOutcome
    result: ExecutionPolicyResult
    analytics_outcome: str | None = None
    analytics_reason: str | None = None


def resolve_confirmation(
    result: ExecutionPolicyResult,
    *,
    trust_mode: bool,
    is_tty: bool,
) -> ConfirmationPlan:
    """Resolve a policy result into a confirmation decision, with no side effects.

    Pure function: no console, no ``input``, no analytics. The interaction layer
    (``interactive_shell.ui.execution_confirm``) renders the decision and emits
    analytics.
    """
    if result.verdict == "deny":
        return ConfirmationPlan(
            outcome=ConfirmationOutcome.DENY,
            result=result,
            analytics_outcome="blocked",
            analytics_reason=result.reason,
        )

    if result.verdict == "allow":
        return ConfirmationPlan(
            outcome=ConfirmationOutcome.ALLOW,
            result=result,
            analytics_outcome="allowed",
            analytics_reason=result.reason,
        )

    # ask
    if trust_mode:
        return ConfirmationPlan(
            outcome=ConfirmationOutcome.ALLOW,
            result=result,
            analytics_outcome="allowed",
            analytics_reason="trust_mode_skipped_prompt",
        )

    if not is_tty:
        return ConfirmationPlan(
            outcome=ConfirmationOutcome.BLOCK_NON_TTY,
            result=result,
            analytics_outcome="blocked",
            analytics_reason="non_interactive_stdin",
        )

    return ConfirmationPlan(
        outcome=ConfirmationOutcome.NEEDS_CONFIRMATION,
        result=result,
    )


def allow_tool(tool_type: str) -> ExecutionPolicyResult:
    """Default-allow verdict for a tool launch.

    Under alpha the policy never denies a tool launch (slash commands,
    investigations, synthetic tests, code-agent launches, LLM runtime switches),
    so every caller resolves to ``allow``. ``tool_type`` is carried through for
    analytics and confirmation UX.
    """
    return ExecutionPolicyResult(verdict="allow", tool_type=tool_type, reason=None)


def plan_foreground_tool(
    tool_type: str,
    classification: str | None = None,
) -> ToolExecutionPlan:
    """Build a FOREGROUND execution plan around a default-allow verdict."""
    return ToolExecutionPlan(
        tool_type=tool_type,
        classification=classification or tool_type,
        execution_mode=ToolExecutionMode.FOREGROUND,
        policy=allow_tool(tool_type),
    )


__all__ = [
    "ConfirmationOutcome",
    "ConfirmationPlan",
    "ExecutionPolicyResult",
    "ExecutionVerdict",
    "ToolExecutionMode",
    "ToolExecutionPlan",
    "allow_tool",
    "plan_foreground_tool",
    "resolve_confirmation",
]
