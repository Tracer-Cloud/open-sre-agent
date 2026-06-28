"""Shell execution tool."""

from __future__ import annotations

from typing import Any

from tools.interactive_shell.contracts import (
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
    object_schema,
    string_property,
)
from tools.interactive_shell.shell.runner import (
    run_shell_command,
)


def execute_shell_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
    command = str(args.get("command", "")).strip()
    if not command:
        return False
    run_shell_command(
        command,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


TOOL_ENTRY = ToolEntry(
    name="shell_run",
    description=(
        "Run a narrowly scoped local diagnostic shell command. Use for read-only inspection "
        "or controlled operational steps already requested by the user; avoid destructive, "
        "credential-exfiltrating, or unrelated commands."
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
            )
        },
        required=("command",),
    ),
    execute=execute_shell_tool,
    is_available=lambda session: capability_not_explicitly_disabled(session, "shell_commands"),
)


__all__ = ["TOOL_ENTRY", "execute_shell_tool"]
