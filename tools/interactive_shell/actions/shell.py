"""Shell execution tool."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    capability_available_from_sources,
    execute_with_action_context,
    object_schema,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool
from tools.interactive_shell.shell.runner import run_shell_command
from tools.interactive_shell.subprocess import require_subprocess_presenter


def _coerce_quiet(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def execute_shell_tool(args: dict[str, Any], ctx: ActionToolContext) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        return {"ok": False, "command": "", "response_text": "missing shell command"}
    quiet = _coerce_quiet(args.get("quiet", False))
    return run_shell_command(command, require_subprocess_presenter(ctx), quiet=quiet)


def run_shell(*, command: str, context: Any, quiet: bool = False) -> dict[str, Any]:
    return execute_with_action_context(
        {"command": command, "quiet": quiet},
        context,
        execute_shell_tool,
    )


shell_run_tool = RegisteredTool(
    name="shell_run",
    description=(
        "Run a narrowly scoped local diagnostic shell command. Use for read-only inspection "
        "or controlled operational steps already requested by the user; avoid destructive, "
        "credential-exfiltrating, or unrelated commands. Set quiet=true to hide the $ line "
        "and stdout/stderr from the terminal while still returning output to the agent "
        "(required for architecture-audit probes)."
    ),
    input_schema=object_schema(
        properties={
            "command": string_property(
                description=(
                    "Exact shell command to execute. Prefer safe diagnostics (for example: "
                    "`ls`, `pwd`, `git status`, `uv run python -m pytest ...`). Do not use "
                    "commands that wipe data or alter unrelated system state."
                ),
                min_length=1,
            ),
            "quiet": {
                "type": "boolean",
                "description": (
                    "When true, do not print the command line or stdout/stderr to the "
                    "interactive shell. Tool result payload is unchanged. Use for "
                    "architecture-audit agent-scan and heuristic passes."
                ),
            },
        },
        required=("command",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_shell,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


__all__ = ["execute_shell_tool", "shell_run_tool"]
