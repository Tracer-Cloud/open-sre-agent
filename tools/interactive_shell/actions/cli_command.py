"""CLI command tool."""

from __future__ import annotations

from typing import Any

from interactive_shell.runtime.subprocess_runner import (
    run_opensre_cli_command,
)
from tools.interactive_shell.contracts import (
    ToolContext,
    capability_available_from_sources,
    execute_with_repl_context,
    object_schema,
    string_property,
)
from tools.registered_tool import RegisteredTool


def execute_cli_command_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
    payload = str(args.get("payload", "")).strip()
    if not payload:
        return False
    run_opensre_cli_command(
        payload,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
    )
    return True


def run_cli_command(*, payload: str, context: Any) -> dict[str, Any]:
    return execute_with_repl_context({"payload": payload}, context, execute_cli_command_tool)


cli_exec_tool = RegisteredTool(
    name="cli_exec",
    description=(
        "Run an `opensre` CLI subcommand payload (without the leading `opensre ` prefix). "
        "Prefer allowed operational families such as health/status/list/show/integrations/"
        "synthetic checks; avoid unrelated or dangerous payloads."
    ),
    input_schema=object_schema(
        properties={
            "payload": string_property(
                description=(
                    "CLI payload passed to `opensre` without the leading command prefix "
                    "(for example: `integrations list`, `health`, `synthetic run ...`). "
                    "Must not start with `opensre `."
                ),
                min_length=1,
            )
        },
        required=("payload",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_cli_command,
    is_available=lambda sources: capability_available_from_sources(sources, "cli_commands"),
)


__all__ = ["cli_exec_tool", "execute_cli_command_tool"]
