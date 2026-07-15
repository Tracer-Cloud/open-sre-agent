"""Sample alert tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    capability_available_from_sources,
    execute_with_action_context,
    object_schema,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool
from platform.common.task_types import TaskRecord
from tools.interactive_shell.shared.investigation_launch import (
    InvestigationLaunchPorts,
    InvestigationSession,
    launch_investigation,
)

_SAMPLE_ALERT_TEMPLATES = ("generic",)


def run_sample_alert(
    template_name: str,
    session: InvestigationSession,
    console: Console,
    *,
    ports: InvestigationLaunchPorts,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:

    def _run(task: TaskRecord) -> dict[str, object]:
        return ports.run_sample_alert(
            template_name=template_name,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )

    def _start_background() -> None:

        ports.start_background_sample(
            template_name=template_name,
            session=session,
            console=console,
            display_command=f"sample alert:{template_name}",
        )

    launch_investigation(
        session=session,
        console=console,
        ports=ports,
        tool_type="sample_alert",
        action_summary=f"sample alert investigation ({template_name})",
        announce_label="sample alert",
        announce_value=template_name,
        record_value=f"sample:{template_name}",
        foreground_task_command=f"sample alert:{template_name}",
        exception_context="surfaces.interactive_shell.sample_alert",
        run=_run,
        start_background=_start_background,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


def execute_sample_alert_tool(args: dict[str, Any], ctx: ActionToolContext) -> bool:
    template = str(args.get("template", "")).strip()
    if not template:
        return False
    if ctx.investigation_ports is None:
        raise RuntimeError("sample alert tool requires investigation runtime ports")
    run_sample_alert(
        template,
        ctx.session,
        ctx.console,
        ports=ctx.investigation_ports,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


def run_sample_alert_action(*, template: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"template": template},
        context,
        execute_sample_alert_tool,
    )


alert_sample_tool = RegisteredTool(
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
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_sample_alert_action,
    is_available=lambda sources: capability_available_from_sources(
        sources,
        "investigation",
    ),
)


__all__ = ["alert_sample_tool", "execute_sample_alert_tool", "run_sample_alert"]
