"""Factory functions that build ``PlannedAction`` objects from deterministic intent."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    ActionKind,
    PlannedAction,
    default_target_surface,
)


def _deterministic_action(kind: ActionKind, content: str, position: int) -> PlannedAction:
    return PlannedAction(
        kind=kind,
        content=content,
        position=position,
        source="deterministic",
        confidence=1.0,
        target_surface=default_target_surface(kind),
    )


def slash_action(command: str, position: int) -> PlannedAction:
    return _deterministic_action("slash", command, position)


def shell_action(command: str, position: int) -> PlannedAction:
    return _deterministic_action("shell", command, position)


def sample_alert_action(template_name: str, position: int) -> PlannedAction:
    return _deterministic_action("sample_alert", template_name, position)


def investigation_action(payload: str, position: int) -> PlannedAction:
    return _deterministic_action("investigation", payload, position)


def synthetic_test_action(suite_name: str, position: int) -> PlannedAction:
    return _deterministic_action("synthetic_test", suite_name, position)


def task_cancel_action(target: str, position: int) -> PlannedAction:
    return _deterministic_action("task_cancel", target, position)


def implementation_action(request: str, position: int) -> PlannedAction:
    return _deterministic_action("implementation", request, position)


def llm_provider_action(provider: str, position: int) -> PlannedAction:
    return _deterministic_action("llm_provider", provider, position)


def cli_command_action(args: str, position: int) -> PlannedAction:
    return _deterministic_action("cli_command", args, position)
