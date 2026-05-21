"""Sample alert action tool."""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_sample_alert,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    object_schema,
    string_property,
)

_SAMPLE_ALERT_TEMPLATES = ("generic",)


def execute_sample_alert_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    template = str(args.get("template", "")).strip()
    if not template:
        return False
    run_sample_alert(
        template,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


TOOL_ENTRY = ToolEntry(
    name="alert_sample",
    description="Run a sample alert template.",
    input_schema=object_schema(
        properties={
            "template": string_property(
                description="Sample alert template name to run.",
                enum=_SAMPLE_ALERT_TEMPLATES,
            )
        },
        required=("template",),
    ),
    execution_tier=ExecutionTier.ELEVATED,
    execute=execute_sample_alert_action,
)


__all__ = ["TOOL_ENTRY", "execute_sample_alert_action"]
