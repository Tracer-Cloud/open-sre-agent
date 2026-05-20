"""Task cancellation action tool."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.markup import escape

from app.cli.interactive_shell.commands import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    evaluate_slash_tier,
    execution_allowed,
    resolve_slash_execution_tier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    REGISTRY,
    ToolContext,
    ToolEntry,
    object_schema,
)
from app.cli.interactive_shell.runtime import TaskKind, TaskStatus


def _running_task_matches(ctx: ToolContext, target: str) -> Sequence[object]:
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


def _resolve_task_cancel_target(ctx: ToolContext, target: str) -> str | None:
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


def execute_task_cancel_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    target = str(args.get("target", "")).strip()
    if not target:
        return False
    task_id = _resolve_task_cancel_target(ctx, target)
    if task_id is None:
        return True
    command = f"/cancel {task_id}"
    cmd = SLASH_COMMANDS["/cancel"]
    tier = resolve_slash_execution_tier("/cancel", [task_id], cmd.execution_tier)
    policy = evaluate_slash_tier(tier)
    if not execution_allowed(
        policy,
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
    dispatch_slash(
        command,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        policy_precleared=True,
    )
    return True


REGISTRY.register(
    ToolEntry(
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
        execution_tier=ExecutionTier.ELEVATED,
        execute=execute_task_cancel_action,
    )
)
