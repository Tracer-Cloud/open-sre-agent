"""Registry for interactive-shell action tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    ActionKind,
)
from app.cli.interactive_shell.runtime.session import ReplSession

ToolExecutor = Callable[[dict[str, Any], "ToolContext"], bool]
ToolAvailability = Callable[[ReplSession], bool]
ToolSchema = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    session: ReplSession
    console: Console
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None
    action_already_listed: bool = True


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    execution_tier: ExecutionTier
    execute: ToolExecutor
    is_available: ToolAvailability = lambda _session: True


def string_property(
    *,
    description: str,
    enum: tuple[str, ...] | None = None,
    min_length: int | None = None,
) -> ToolSchema:
    schema: ToolSchema = {"type": "string", "description": description}
    if enum:
        schema["enum"] = list(enum)
    if min_length is not None:
        schema["minLength"] = min_length
    return schema


def string_array_property(*, description: str) -> ToolSchema:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


def object_schema(*, properties: dict[str, ToolSchema], required: tuple[str, ...]) -> ToolSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def capability_not_explicitly_disabled(session: ReplSession, capability_name: str) -> bool:
    available_capabilities = getattr(session, "available_capabilities", {})
    capability_values = (
        available_capabilities.get(capability_name)
        if isinstance(available_capabilities, dict)
        else None
    )
    return not (isinstance(capability_values, tuple) and capability_values == ())


class ActionToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        self._tools[entry.name] = entry

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools.keys()))

    def tool_specs_for_llm(self, session: ReplSession) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for name in self.names():
            entry = self._tools[name]
            if not entry.is_available(session):
                continue
            specs.append(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "input_schema": entry.input_schema,
                }
            )
        return specs

    def dispatch(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> bool:
        entry = self.get(tool_name)
        if entry is None:
            return False
        return entry.execute(args, ctx)


# NOTE: Tool names MUST match the regex ``^[a-zA-Z0-9_-]+$`` — the OpenAI
# Chat Completions API rejects any other character (including ``.``) with
# HTTP 400. The previous dotted form (e.g. ``slash.invoke``) silently
# failed for every OpenAI-style provider (OpenAI, OpenRouter, Gemini,
# Nvidia, Minimax, Ollama). See ``test_tool_names_are_openai_compatible``
# in ``test_tool_registry.py`` for the gate that prevents regressions.
ACTION_KIND_TO_TOOL: dict[ActionKind, str] = {
    "llm_provider": "llm_set_provider",
    "slash": "slash_invoke",
    "shell": "shell_run",
    "sample_alert": "alert_sample",
    "investigation": "investigation_start",
    "synthetic_test": "synthetic_run",
    "task_cancel": "task_cancel",
    "cli_command": "cli_exec",
    "implementation": "code_implement",
    "assistant_handoff": "assistant_handoff",
}

REGISTRY = ActionToolRegistry()
