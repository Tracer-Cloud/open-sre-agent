"""Action plan parsing and capability validation for the terminal assistant."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.cli.interactive_shell.runtime import ReplSession


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
def _registered_interactive_command(command: str) -> bool:
    parts = command.strip().split()
    if not parts:
        return False
    name = parts[0].lower()
    if name == "/":
        return True
    if not name.startswith("/"):
        return False

    from app.cli.interactive_shell.command_registry import SLASH_COMMANDS

    return name in SLASH_COMMANDS


_ALLOWED_SLASH_ACTIONS = frozenset(
    {
        "/model show",
        "/health",
        "/doctor",
        "/version",
    }
)

# Conversational action kinds map onto the same capability gates the action
# planner uses, so a session that explicitly disables a surface cannot actuate
# it from the chat answer path either.
_ACTION_CAPABILITY: dict[str, str] = {
    "switch_llm_provider": "llm_provider",
    "switch_toolcall_model": "llm_provider",
    "slash": "slash_commands",
    "run_interactive": "slash_commands",
    "run_cli_command": "cli_commands",
}


def _actions_allowed_by_capabilities(
    actions: list[dict[str, object]], session: ReplSession
) -> list[dict[str, object]]:
    """Drop actions whose capability surface is explicitly disabled for *session*."""
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
        capability_not_explicitly_disabled,
    )

    allowed: list[dict[str, object]] = []
    for action in actions:
        capability = _ACTION_CAPABILITY.get(str(action.get("action", "")).strip())
        if capability is None or capability_not_explicitly_disabled(session, capability):
            allowed.append(action)
    return allowed


def _opensre_integration_command_blocked(payload: str, session: ReplSession) -> bool:
    """Block integration-management CLI runs when the session has none configured."""
    if not session.configured_integrations_known or session.configured_integrations:
        return False
    lowered = payload.strip().lower()
    return lowered.startswith("integrations") or "integration" in lowered


def _extract_json_object(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _normalize_action(action: dict[str, object]) -> dict[str, object] | None:
    normalized = dict(action)
    kind = str(normalized.get("action", "")).strip()
    if not kind and str(normalized.get("provider", "")).strip():
        normalized["action"] = "switch_llm_provider"
        return normalized
    if not kind and str(normalized.get("command", "")).strip():
        normalized["action"] = "slash"
        return normalized
    return normalized if kind else None


def _parse_action_plan(text: str) -> list[dict[str, object]]:
    payload = _extract_json_object(text)
    if payload is None:
        return []
    actions = payload.get("actions")
    if not isinstance(actions, list):
        normalized = _normalize_action(payload)
        return [normalized] if normalized is not None else []
    return [
        normalized
        for action in actions
        if isinstance(action, dict)
        for normalized in [_normalize_action(action)]
        if normalized is not None
    ]


__all__ = [
    "_ACTION_CAPABILITY",
    "_ALLOWED_SLASH_ACTIONS",
    "_actions_allowed_by_capabilities",
    "_extract_json_object",
    "_normalize_action",
    "_opensre_integration_command_blocked",
    "_parse_action_plan",
    "_registered_interactive_command",
]
