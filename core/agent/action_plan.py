"""Parsing of the conversational assistant's action plan (pure).

The conversational assistant may answer with a compact JSON object describing
runtime actions to perform (switch provider, run a slash command, etc.). This
module turns raw model output into a typed, immutable action plan. *Executing*
the plan is a surface concern handled by an :class:`core.agent.ports.ActionDispatch`
adapter; parsing is pure and lives here so both the engine and the dispatcher
share one representation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

# Conversational action kinds map onto capability gates the dispatcher enforces.
_ACTION_CAPABILITY: dict[str, str] = {
    "switch_llm_provider": "llm_provider",
    "switch_toolcall_model": "llm_provider",
    "slash": "slash_commands",
    "run_interactive": "slash_commands",
    "run_cli_command": "cli_commands",
}

# The distinct capability surfaces any action can require. A dispatcher uses this
# to snapshot which surfaces a session has explicitly disabled.
ALL_ACTION_CAPABILITIES: frozenset[str] = frozenset(_ACTION_CAPABILITY.values())


def _as_text(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ActionPlanAction:
    """Typed representation of a single action emitted by the CLI core.agent."""

    kind: str
    provider: str = ""
    model: str = ""
    toolcall_model: str = ""
    command: str = ""
    args: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ActionPlanAction | None:
        kind = _as_text(payload.get("action"))

        if not kind and _as_text(payload.get("provider")):
            kind = "switch_llm_provider"

        if not kind and _as_text(payload.get("command")):
            kind = "slash"

        if not kind:
            return None

        return cls(
            kind=kind,
            provider=_as_text(payload.get("provider")),
            model=_as_text(payload.get("model")),
            toolcall_model=_as_text(payload.get("toolcall_model")),
            command=_as_text(payload.get("command")),
            args=_as_text(payload.get("args")),
        )

    @property
    def capability(self) -> str | None:
        return _ACTION_CAPABILITY.get(self.kind)

    @property
    def label(self) -> str:
        if self.kind == "switch_llm_provider":
            text = f"switch LLM provider to {self.provider}"
            if self.model:
                text += f" ({self.model})"
            if self.toolcall_model:
                text += f" + toolcall {self.toolcall_model}"
            return text

        if self.kind == "switch_toolcall_model":
            return (
                f"switch toolcall model to {self.model}" if self.model else "switch toolcall model"
            )

        if self.kind == "slash":
            return self.command

        if self.kind == "run_cli_command":
            return f"opensre {self.args}" if self.args else "opensre"

        if self.kind == "run_interactive":
            return self.command or "interactive command"

        return f"unsupported action: {self.kind or '?'}"


def extract_json_object(text: str) -> dict[str, object] | None:
    """Find the first top-level JSON object embedded in *text* (pure)."""
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


def parse_action_plan(text: str) -> tuple[ActionPlanAction, ...]:
    """Parse raw model output into an immutable action plan (pure)."""
    payload = extract_json_object(text)
    if payload is None:
        return ()

    actions = payload.get("actions")
    if not isinstance(actions, list):
        single = ActionPlanAction.from_payload(payload)
        return (single,) if single is not None else ()

    return tuple(
        action
        for raw in actions
        if isinstance(raw, dict)
        for action in (ActionPlanAction.from_payload(raw),)
        if action is not None
    )


__all__ = [
    "ALL_ACTION_CAPABILITIES",
    "ActionPlanAction",
    "extract_json_object",
    "parse_action_plan",
]
