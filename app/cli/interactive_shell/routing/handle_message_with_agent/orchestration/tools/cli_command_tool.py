"""CLI command action tool."""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_opensre_cli_command,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    REGISTRY,
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
    object_schema,
    string_property,
)


def execute_cli_command_action(args: dict[str, Any], ctx: ToolContext) -> bool:
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


REGISTRY.register(
    ToolEntry(
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
        execution_tier=ExecutionTier.ELEVATED,
        execute=execute_cli_command_action,
        is_available=lambda session: capability_not_explicitly_disabled(session, "cli_commands"),
    )
)
