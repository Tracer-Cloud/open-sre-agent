"""Registry for interactive-shell action tools."""

from __future__ import annotations

import functools
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    ActionKind,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
)
from app.cli.interactive_shell.runtime.session import ReplSession


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


@functools.cache
def register_action_tools() -> tuple[str, ...]:
    """Explicitly register all action tools from the composition root."""
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools.catalog import (
        ACTION_TOOL_CATALOG,
    )

    for entry in ACTION_TOOL_CATALOG:
        if not isinstance(entry, ToolEntry):
            msg = f"action tool entry must be ToolEntry, got {type(entry)!r}"
            raise TypeError(msg)
        REGISTRY.register(entry)
    return REGISTRY.names()


register_action_tools()


def ensure_action_tools_loaded() -> None:
    """Backward-compatible no-op alias for callers/tests expecting this hook."""
    register_action_tools()
