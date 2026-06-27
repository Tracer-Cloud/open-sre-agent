"""Investigation action tool."""

from __future__ import annotations

from typing import Any

from interactive_shell.harness.orchestration.action_executor import (
    run_text_investigation,
)
from interactive_shell.harness.orchestration.execution_tier import (
    ExecutionTier,
)
from interactive_shell.harness.orchestration.feature_flags import (
    investigation_loop_enabled,
)
from interactive_shell.harness.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    object_schema,
    string_property,
)
from interactive_shell.runtime.core.session import ReplSession


def _investigation_planner_selectable(_session: ReplSession) -> bool:
    """Hide ``investigation_start`` from the planner when the loop is disabled.

    Direct dispatch (e.g. explicit/programmatic ``investigation`` actions) stays
    available; only natural-language planner selection is gated here.
    """
    return investigation_loop_enabled()


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


TOOL_ENTRY = ToolEntry(
    name="investigation_start",
    description=(
        "Start an investigation with the provided alert text. Use whenever the user "
        "explicitly instructs you to investigate, RCA, diagnose, analyze, or "
        "root-cause a named problem — including 'investigate why X ...' — regardless "
        "of CONNECTED INTEGRATIONS. In compound turns like `run /remote and then "
        'investigate "hello world"`, emit this as a separate second tool call; '
        "never drop the quoted investigation after emitting the slash command. "
        "Do NOT use for bare incident statements with no investigate verb, generic "
        "'Run an investigation.' with no subject, sample/demo alerts, or plain data "
        "lookups."
    ),
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
    is_planner_selectable=_investigation_planner_selectable,
)


__all__ = ["TOOL_ENTRY", "execute_investigation_action"]
