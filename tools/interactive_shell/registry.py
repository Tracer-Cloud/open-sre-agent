"""Registry for interactive-shell tools."""

from __future__ import annotations

import functools
from typing import Any, Literal

from core.agent_harness.session import ReplSession
from core.types import AgentTool
from tools.interactive_shell.contracts import (
    ToolContext,
    ToolEntry,
)

ToolKind = Literal[
    "llm_provider",
    "slash",
    "shell",
    "sample_alert",
    "investigation",
    "synthetic_test",
    "task_cancel",
    "cli_command",
    "implementation",
    "assistant_handoff",
]


class ToolRegistry:
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
            if not entry.is_available(session) or not entry.is_planner_selectable(session):
                continue
            specs.append(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "input_schema": entry.input_schema,
                }
            )
        return specs

    def agent_tools_for_context(
        self,
        ctx: ToolContext,
        *,
        planner_selectable_only: bool = True,
    ) -> list[AgentTool]:
        tools: list[AgentTool] = []
        for name in self.names():
            entry = self._tools[name]
            if not entry.is_available(ctx.session):
                continue
            if planner_selectable_only and not entry.is_planner_selectable(ctx.session):
                continue
            tools.append(entry.to_agent_tool(ctx))
        return tools


# NOTE: Tool names MUST match the regex ``^[a-zA-Z0-9_-]+$`` — the OpenAI
# Chat Completions API rejects any other character (including ``.``) with
# HTTP 400. The previous dotted form (e.g. ``slash.invoke``) silently
# failed for every OpenAI-style provider (OpenAI, OpenRouter, Gemini,
# Nvidia, Minimax, Ollama). See ``test_tool_names_are_openai_compatible``
# in ``test_tool_registry.py`` for the gate that prevents regressions.
TOOL_KIND_TO_NAME: dict[ToolKind, str] = {
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

REGISTRY = ToolRegistry()


@functools.cache
def register_tools() -> tuple[str, ...]:
    """Explicitly register all tools from the composition root."""
    from tools.interactive_shell.catalog import (
        TOOL_CATALOG,
    )

    for entry in TOOL_CATALOG:
        if not isinstance(entry, ToolEntry):
            msg = f"tool entry must be ToolEntry, got {type(entry)!r}"
            raise TypeError(msg)
        REGISTRY.register(entry)
    return REGISTRY.names()


register_tools()
