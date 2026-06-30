"""Shell execution tool."""

from __future__ import annotations

from typing import Any

from tools.interactive_shell.contracts import (
    ToolContext,
    capability_available_from_sources,
    execute_with_repl_context,
    object_schema,
    string_property,
)
from tools.interactive_shell.shell.runner import (
    run_shell_command,
)
from tools.registered_tool import RegisteredTool


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


def run_shell(*, command: str, context: Any) -> dict[str, Any]:
    return execute_with_repl_context({"command": command}, context, execute_shell_tool)


shell_run_tool = RegisteredTool(
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
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_shell,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


__all__ = ["execute_shell_tool", "shell_run_tool"]
