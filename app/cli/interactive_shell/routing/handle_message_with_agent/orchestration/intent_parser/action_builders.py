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


def shell_action(command: str, position: int) -> PlannedAction:
    return _deterministic_action("shell", command, position)
