"""Execution of the conversational assistant's action plan (shell surface).

Parsing the action plan is pure and lives in :mod:`core.agent_harness.action_plan`. *Executing*
it — capability gating, confirmation, slash/CLI/provider dispatch — is a terminal
concern and lives here. This module is the shell-side implementation behind the
:class:`core.agent_harness.ports.ActionDispatch` port.

Structured as a functional core (pure planners that emit
:class:`HarnessInstruction` values) wrapped by a thin effect interpreter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.markup import escape

from core.agent_harness.action_plan import ALL_ACTION_CAPABILITIES, ActionPlanAction
from interactive_shell.runtime import ReplSession
from interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    STREAM_LABEL_ASSISTANT,
    WARNING,
)

_ALLOWED_SLASH_ACTIONS = frozenset(
    {
        "/model show",
        "/health",
        "/doctor",
        "/version",
    }
)


# ---------------------------------------------------------------------------
# Capability filtering (pure core + snapshot adapter)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilitySnapshot:
    """Immutable view of which capability surfaces are explicitly disabled."""

    disabled_capabilities: frozenset[str]


def _filter_actions_by_capabilities(
    actions: tuple[ActionPlanAction, ...], capabilities: CapabilitySnapshot
) -> tuple[ActionPlanAction, ...]:
    """Drop actions whose capability surface is explicitly disabled (pure)."""
    return tuple(
        action
        for action in actions
        if action.capability is None or action.capability not in capabilities.disabled_capabilities
    )


def _read_capability_snapshot(session: ReplSession) -> CapabilitySnapshot:
    """Snapshot the session's disabled capability surfaces once."""
    from tools.interactive_shell.contracts import capability_not_explicitly_disabled

    disabled = frozenset(
        capability
        for capability in ALL_ACTION_CAPABILITIES
        if not capability_not_explicitly_disabled(session, capability)
    )
    return CapabilitySnapshot(disabled_capabilities=disabled)


# ---------------------------------------------------------------------------
# Action planning environment (snapshot) + pure predicates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionPlanningEnv:
    """Immutable snapshot of everything the pure action planner needs."""

    allowed_slash_actions: frozenset[str]
    registered_slash_commands: frozenset[str]
    configured_integrations_known: bool
    configured_integrations_count: int
    capabilities: CapabilitySnapshot
    repl_tty_interactive: bool


def _read_action_planning_env(session: ReplSession) -> ActionPlanningEnv:
    """Read the live world once into a frozen planning environment."""
    from interactive_shell.command_registry import SLASH_COMMANDS
    from interactive_shell.ui.components.choice_menu import repl_tty_interactive

    return ActionPlanningEnv(
        allowed_slash_actions=_ALLOWED_SLASH_ACTIONS,
        registered_slash_commands=frozenset(SLASH_COMMANDS),
        configured_integrations_known=session.configured_integrations_known,
        configured_integrations_count=len(session.configured_integrations),
        capabilities=_read_capability_snapshot(session),
        repl_tty_interactive=repl_tty_interactive(),
    )


# `run_interactive` is not a narrow feature allowlist. It is the bridge from an
# agent-planned action back into the OpenSRE interactive shell. Any command that
# is registered in the slash-command registry is already an OpenSRE command and
# must stay eligible here.
#
# Keep this registry-backed instead of listing subcommands like
# `/integrations setup` or `/integrations remove`: duplicating subcommand lists
# here drifts from the actual dispatcher and causes valid OpenSRE commands to be
# rejected before the normal policy/confirmation flow can evaluate them. The
# dispatcher remains the source of truth for argument validation, execution tier,
# confirmation, exclusive-stdin handling, and the command's side effects.
#
# The only thing this gate should reject is non-OpenSRE input: empty strings,
# shell snippets, arbitrary text, or unknown slash commands. Do not reintroduce
# a per-command allowlist in this file.
def _registered_interactive_command(command: str, registered: frozenset[str]) -> bool:
    """True when *command* names a registered OpenSRE slash command (pure)."""
    parts = command.strip().split()
    if not parts:
        return False
    name = parts[0].lower()
    if name == "/":
        return True
    if not name.startswith("/"):
        return False
    return name in registered


def _integration_command_blocked(payload: str, env: ActionPlanningEnv) -> bool:
    """Block integration-management CLI runs when none are configured (pure)."""
    if not env.configured_integrations_known or env.configured_integrations_count:
        return False
    lowered = payload.strip().lower()
    return lowered.startswith("integrations") or "integration" in lowered


# ---------------------------------------------------------------------------
# Harness effects + pure action planners
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessEffect:
    """A single, fully-described side effect for the interpreter to perform."""

    type: Literal[
        "print",
        "switch_llm_provider",
        "switch_toolcall_model",
        "dispatch_slash",
        "run_opensre_cli",
        "queue_interactive_command",
        "record_session",
    ]
    message: str = ""
    command: str = ""
    action: ActionPlanAction | None = None
    policy_precleared: bool = False
    ok: bool = True


@dataclass(frozen=True)
class ConfirmedEffects:
    """Effects gated behind an execution-policy confirmation.

    The interpreter runs ``on_allowed`` if the policy/confirmation clears, and
    ``on_denied`` otherwise. Modeling the branch explicitly keeps confirmation
    control flow visible and testable instead of buried in imperative returns.
    """

    policy_tool: str
    summary: str
    on_allowed: tuple[HarnessEffect, ...]
    on_denied: tuple[HarnessEffect, ...] = ()


HarnessInstruction = HarnessEffect | ConfirmedEffects


def _print(message: str) -> HarnessEffect:
    return HarnessEffect(type="print", message=message)


def _print_error(message: str) -> HarnessEffect:
    return HarnessEffect(type="print", message=f"[{ERROR}]{escape(message)}[/]")


def _plan_switch_llm_provider(action: ActionPlanAction) -> tuple[HarnessInstruction, ...]:
    if not action.provider:
        return (_print_error("missing provider for switch_llm_provider action"),)

    slash_label = f"/model set {action.provider}"
    if action.model:
        slash_label += f" {action.model}"
    if action.toolcall_model:
        slash_label += f" --toolcall-model {action.toolcall_model}"

    return (
        ConfirmedEffects(
            policy_tool="switch_llm_provider",
            summary=slash_label,
            on_allowed=(
                _print(f"[bold]$ {escape(slash_label)}[/bold]"),
                HarnessEffect(type="switch_llm_provider", action=action),
                HarnessEffect(type="record_session", command=slash_label, ok=True),
            ),
        ),
    )


def _plan_switch_toolcall_model(action: ActionPlanAction) -> tuple[HarnessInstruction, ...]:
    if not action.model:
        return (_print_error("missing model for switch_toolcall_model action"),)

    command = f"/model toolcall set {action.model}"

    return (
        ConfirmedEffects(
            policy_tool="switch_toolcall_model",
            summary=command,
            on_allowed=(
                _print(f"[bold]$ {escape(command)}[/bold]"),
                HarnessEffect(type="switch_toolcall_model", action=action),
                HarnessEffect(type="record_session", command=command, ok=True),
            ),
        ),
    )


def _plan_slash_action(
    action: ActionPlanAction, env: ActionPlanningEnv
) -> tuple[HarnessInstruction, ...]:
    command = action.command
    if command not in env.allowed_slash_actions:
        return (_print_error(f"unsupported action command: {command}"),)

    stripped = command.strip()
    name = stripped.split()[0].lower()

    # Unknown to the dispatcher: hand straight to dispatch_slash, which renders
    # its own "unknown command" feedback (no policy preclear).
    if name not in env.registered_slash_commands:
        return (HarnessEffect(type="dispatch_slash", command=command, policy_precleared=False),)

    return (
        ConfirmedEffects(
            policy_tool="slash",
            summary=stripped,
            on_allowed=(
                _print(f"[bold]$ {escape(command)}[/bold]"),
                HarnessEffect(type="dispatch_slash", command=command, policy_precleared=True),
            ),
            on_denied=(HarnessEffect(type="record_session", command=stripped, ok=False),),
        ),
    )


def _plan_cli_command(
    action: ActionPlanAction, env: ActionPlanningEnv
) -> tuple[HarnessInstruction, ...]:
    if not action.args:
        return (_print_error("missing args for run_cli_command action"),)

    if _integration_command_blocked(action.args, env):
        return (
            _print(
                f"[{WARNING}]integration command blocked: no integrations are configured "
                "in this session.[/]"
            ),
        )

    return (HarnessEffect(type="run_opensre_cli", command=action.args),)


def _plan_interactive_command(
    action: ActionPlanAction, env: ActionPlanningEnv
) -> tuple[HarnessInstruction, ...]:
    command = action.command

    if not _registered_interactive_command(command, env.registered_slash_commands):
        return (_print_error(f"unsupported interactive command: {command}"),)

    if not env.repl_tty_interactive:
        return (
            _print(f"Run [bold]{escape(command)}[/bold] in the interactive shell to continue."),
        )

    return (
        _print(f"[{DIM}]Launching[/] [{BOLD_BRAND}]{escape(command)}[/]…"),
        HarnessEffect(type="queue_interactive_command", command=command),
    )


def _plan_action_effects(
    action: ActionPlanAction, env: ActionPlanningEnv
) -> tuple[HarnessInstruction, ...]:
    """Translate one action into the instructions that realize it (pure)."""
    if action.kind == "switch_llm_provider":
        return _plan_switch_llm_provider(action)
    if action.kind == "switch_toolcall_model":
        return _plan_switch_toolcall_model(action)
    if action.kind == "slash":
        return _plan_slash_action(action, env)
    if action.kind == "run_cli_command":
        return _plan_cli_command(action, env)
    if action.kind == "run_interactive":
        return _plan_interactive_command(action, env)
    return (_print_error(f"unsupported action: {action.kind or '?'}"),)


def _plan_requested_actions_header(
    actions: tuple[ActionPlanAction, ...],
) -> tuple[HarnessInstruction, ...]:
    numbered = [
        _print(f"[{DIM}]{index}.[/] [{BOLD_BRAND}]{escape(action.label)}[/]")
        for index, action in enumerate(actions, start=1)
    ]
    return (
        _print(""),
        _print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]"),
        _print(f"[{DIM}]Requested actions:[/]"),
        *numbered,
        _print(""),
    )


def _plan_action_plan_effects(
    actions: tuple[ActionPlanAction, ...], env: ActionPlanningEnv
) -> tuple[HarnessInstruction, ...]:
    """Plan the full action-plan execution as one instruction stream (pure)."""
    instructions: list[HarnessInstruction] = list(_plan_requested_actions_header(actions))
    for action in actions:
        instructions.append(_print(""))
        instructions.extend(_plan_action_effects(action, env))
    instructions.append(_print(""))
    return tuple(instructions)


# ---------------------------------------------------------------------------
# Effect interpreter (the single imperative edge for action execution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionRuntime:
    """Boundary objects the interpreter needs to perform effects."""

    session: ReplSession
    console: Console
    confirm_fn: Callable[[str], str] | None
    is_tty: bool | None


def _confirm_instruction(instruction: ConfirmedEffects, runtime: ActionRuntime) -> bool:
    from interactive_shell.ui.execution_confirm import execution_allowed
    from tools.interactive_shell.shared import allow_tool

    return execution_allowed(
        allow_tool(instruction.policy_tool),
        session=runtime.session,
        console=runtime.console,
        action_summary=instruction.summary,
        confirm_fn=runtime.confirm_fn,
        is_tty=runtime.is_tty,
        action_already_listed=True,
    )


def _interpret_effect(effect: HarnessEffect, runtime: ActionRuntime) -> None:
    console = runtime.console
    session = runtime.session
    match effect.type:
        case "print":
            console.print(effect.message)
        case "switch_llm_provider":
            from interactive_shell.command_registry import switch_llm_provider

            action = effect.action
            assert action is not None  # planner always attaches the action
            switch_llm_provider(
                action.provider,
                console,
                model=action.model or None,
                toolcall_model=action.toolcall_model or None,
            )
        case "switch_toolcall_model":
            from interactive_shell.command_registry import switch_toolcall_model

            action = effect.action
            assert action is not None  # planner always attaches the action
            switch_toolcall_model(action.model, console)
        case "dispatch_slash":
            from interactive_shell.command_registry import dispatch_slash

            dispatch_slash(
                effect.command,
                session,
                console,
                confirm_fn=runtime.confirm_fn,
                is_tty=runtime.is_tty,
                policy_precleared=effect.policy_precleared,
            )
        case "run_opensre_cli":
            from interactive_shell.runtime.subprocess_runner import run_opensre_cli_command

            run_opensre_cli_command(
                effect.command,
                session,
                console,
                confirm_fn=runtime.confirm_fn,
                is_tty=runtime.is_tty,
            )
        case "queue_interactive_command":
            session.queue_auto_command(effect.command)
        case "record_session":
            session.record("slash", effect.command, ok=effect.ok)
        case _:
            raise ValueError(f"unknown harness effect type: {effect.type!r}")


def _interpret_instruction(instruction: HarnessInstruction, runtime: ActionRuntime) -> None:
    if isinstance(instruction, ConfirmedEffects):
        branch = (
            instruction.on_allowed
            if _confirm_instruction(instruction, runtime)
            else instruction.on_denied
        )
        _interpret_instructions(branch, runtime)
        return
    _interpret_effect(instruction, runtime)


def _interpret_instructions(
    instructions: tuple[HarnessInstruction, ...], runtime: ActionRuntime
) -> None:
    for instruction in instructions:
        _interpret_instruction(instruction, runtime)


def execute_action_plan(
    actions: tuple[ActionPlanAction, ...],
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Plan and perform an action plan; return True iff anything was eligible."""
    if not actions:
        return False

    env = _read_action_planning_env(session)
    allowed = _filter_actions_by_capabilities(tuple(actions), env.capabilities)
    if not allowed:
        return False

    _interpret_instructions(
        _plan_action_plan_effects(allowed, env),
        ActionRuntime(session=session, console=console, confirm_fn=confirm_fn, is_tty=is_tty),
    )
    return True


__all__ = ["execute_action_plan"]
