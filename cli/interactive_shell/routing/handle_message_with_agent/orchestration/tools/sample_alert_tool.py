"""Sample alert action tool."""

from __future__ import annotations

from typing import Any

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_sample_alert,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
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
    description=(
        "Run the built-in synthetic sample alert end-to-end (read alert → "
        "investigate → diagnose). Use for any request to run/try/start/launch/"
        "fire/trigger/investigate/look at a 'sample alert', 'test alert', or "
        "'demo alert' (e.g. 'investigate a sample test alert?', 'kick off a "
        "sample alert'). These requests carry NO real pasted alert text — that "
        "is what separates them from investigation_start. Prefer this over "
        "investigation_start and assistant_handoff for sample/test/demo alerts, "
        "regardless of the verb or a trailing '?'."
    ),
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
