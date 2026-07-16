"""Task cancellation tool."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.markup import escape

from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    capability_available_from_sources,
    execute_with_action_context,
    object_schema,
)
from core.tool_framework.registered_tool import RegisteredTool
from platform.common.task_types import TaskKind, TaskStatus
from tools.interactive_shell.shared import plan_foreground_tool


def _running_task_matches(ctx: ActionToolContext, target: str) -> Sequence[object]:
    running = [
        task
        for task in ctx.session.task_registry.list_recent(n=50)
        if task.status == TaskStatus.RUNNING
    ]
    if target == "synthetic_test":
        return [task for task in running if task.kind == TaskKind.SYNTHETIC_TEST]
    if target == "task":
        return running
    return []


def _resolve_task_cancel_target(ctx: ActionToolContext, target: str) -> str | None:
    if target in {"synthetic_test", "task"}:
        matches = _running_task_matches(ctx, target)
        if not matches:
            ctx.console.print(
                f"[dim]no running {escape(target)} task found. use[/] [bold]/tasks[/bold]"
            )
            ctx.session.record("slash", f"/cancel {target}", ok=False)
            return None
        if len(matches) > 1:
            ids = ", ".join(str(getattr(task, "task_id", "")) for task in matches)
            ctx.console.print(
                f"[yellow]multiple running tasks match {escape(target)}:[/] "
                f"{escape(ids)} [dim](run /cancel <id>)[/]"
            )
            ctx.session.record("slash", f"/cancel {target}", ok=False)
            return None
        return str(getattr(matches[0], "task_id", ""))

    candidates = ctx.session.task_registry.candidates(target)
    if not candidates:
        ctx.console.print(f"[red]no task matches id:[/] {escape(target)}")
        ctx.session.record("slash", f"/cancel {target}", ok=False)
        return None
    if len(candidates) > 1:
        ctx.console.print(
            f"[red]ambiguous id prefix:[/] {escape(target)} "
            f"[dim]({len(candidates)} matches — use a longer prefix)[/]"
        )
        ctx.session.record("slash", f"/cancel {target}", ok=False)
        return None
    return str(candidates[0].task_id)


def execute_task_cancel_tool(args: dict[str, Any], ctx: ActionToolContext) -> bool:
    target = str(args.get("target", "")).strip()
    if not target:
        return False
    if ctx.task_cancel_ports is None:
        raise RuntimeError("task cancel tool requires cancellation runtime ports")
    task_id = _resolve_task_cancel_target(ctx, target)
    if task_id is None:
        return True
    command = f"/cancel {task_id}"
    plan = plan_foreground_tool("slash", "slash")
    if not ctx.task_cancel_ports.execution_allowed(
        policy=plan.policy,
        session=ctx.session,
        console=ctx.console,
        action_summary=command,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        ctx.session.record("slash", command, ok=False)
        return True
    ctx.console.print(f"[bold]$ {escape(command)}[/bold]")
    ctx.task_cancel_ports.dispatch_cancel(
        command,
        session=ctx.session,
        console=ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
    )
    return True


def run_task_cancel(*, target: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"target": target}, context, execute_task_cancel_tool)


task_cancel_tool = RegisteredTool(
    name="task_cancel",
    description="Cancel a running task by id or kind.",
    input_schema=object_schema(
        properties={
            "target": {
                "oneOf": [
                    {"type": "string", "enum": ["synthetic_test", "task"]},
                    {"type": "string", "pattern": "^[A-Za-z0-9_-]{3,}$"},
                ],
                "description": (
                    "Task selector: `synthetic_test` to cancel the one running synthetic task, "
                    "`task` to cancel a single running task of any kind, or a task id/prefix "
                    "for `/cancel <id>` resolution."
                ),
            }
        },
        required=("target",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_task_cancel,
    is_available=lambda sources: capability_available_from_sources(
        sources,
        "task_cancel",
    ),
)


__all__ = ["execute_task_cancel_tool", "task_cancel_tool"]
