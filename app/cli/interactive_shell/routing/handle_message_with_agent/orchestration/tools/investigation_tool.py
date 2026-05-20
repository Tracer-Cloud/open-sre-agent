"""Investigation action tool."""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_text_investigation,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    REGISTRY,
    ToolContext,
    ToolEntry,
    object_schema,
    string_property,
)


def execute_investigation_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    alert_text = str(args.get("alert_text", "")).strip()
    if not alert_text:
        return False
    run_text_investigation(
        alert_text,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


REGISTRY.register(
    ToolEntry(
        name="investigation_start",
        description="Start an investigation with the provided alert text.",
        input_schema=object_schema(
            properties={
                "alert_text": string_property(
                    description="Alert text or incident details to investigate.",
                    min_length=1,
                )
            },
            required=("alert_text",),
        ),
        execution_tier=ExecutionTier.ELEVATED,
        execute=execute_investigation_action,
    )
)
