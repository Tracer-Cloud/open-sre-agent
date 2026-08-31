"""Investigation tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from infrastructure.scheduling.task_types import TaskRecord
from tools.interactive_shell.shared.investigation_launch import (
    InvestigationLaunchPorts,
    InvestigationSession,
    launch_investigation,
)


def normalize_investigation_alert_text(raw: str) -> str:
    """Strip outer quotes models often echo from user-quoted investigation payloads."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def run_text_investigation(
    alert_text: str,
    session: InvestigationSession,
    console: Console,
    *,
    ports: InvestigationLaunchPorts,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    def _run(task: TaskRecord) -> dict[str, object]:
        from infrastructure.analytics.investigation_tracker import track_investigation
        from infrastructure.analytics.source import EntrypointSource, TriggerMode

        with (
            track_investigation(
                entrypoint=EntrypointSource.CLI_PASTE,
                trigger_mode=TriggerMode.PASTE,
                interactive=True,
                session=session,  # type: ignore[arg-type]
                investigation_target=alert_text[:120] or None,
            ) as tracker
        ):
            final_state = ports.run_text_investigation(
                alert_text=alert_text,
                context_overrides=session.accumulated_context or None,
                cancel_requested=task.cancel_requested,
                console=console,
            )
            tracker.record_loop_metrics_from_state(final_state)
            return final_state

    def _start_background() -> None:
        ports.start_background_text(
            alert_text=alert_text,
            session=session,
            console=console,
            display_command="background free-text investigation",
        )

    launch_investigation(
        session=session,
        console=console,
        ports=ports,
        tool_type="investigation",
        action_summary=f'investigation from text "{alert_text}"',
        announce_label="investigation",
        announce_value=alert_text,
        record_value=alert_text,
        foreground_task_command=f"investigate:{alert_text}",
        exception_context="surfaces.interactive_shell.text_investigation",
        run=_run,
        start_background=_start_background,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


def execute_investigation_tool(args: dict[str, Any], ctx: ActionToolScope) -> bool:
    alert_text = normalize_investigation_alert_text(str(args.get("alert_text", "")))
    if not alert_text:
        return False
    if ctx.investigation_ports is None:
        raise RuntimeError("investigation tool requires investigation runtime ports")
    run_text_investigation(
        alert_text,
        ctx.session,
        ctx.console,
        ports=ctx.investigation_ports,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


def run_investigation(*, alert_text: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"alert_text": alert_text},
        context,
        execute_investigation_tool,
    )


investigation_start_tool = RegisteredTool(
    name="investigation_start",
    description=(
        "Start an investigation with the provided alert text or quoted payload. "
        "Use whenever the user explicitly instructs you to investigate, RCA, "
        "diagnose, analyze, root-cause, or send an investigation payload — including "
        "'investigate why X ...', pasted alert JSON, and placeholder quoted text "
        "like 'hello world' — regardless of CONNECTED INTEGRATIONS. Call this "
        "immediately with the user's payload as alert_text; do not call "
        "ask_user_choice first to refine onset, shape, blast radius, or signal "
        "flavor. In compound turns like `run /remote and then investigate "
        '"hello world"`, emit this as a separate second tool call; never drop '
        "the quoted investigation after emitting the slash command. "
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
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.EXTERNAL,
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_investigation,
    is_available=lambda sources: capability_available_from_sources(
        sources,
        "investigation",
    ),
)


__all__ = [
    "execute_investigation_tool",
    "investigation_start_tool",
    "normalize_investigation_alert_text",
    "run_text_investigation",
]
