"""Implementation action tool."""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_claude_code_implementation,
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


def execute_implementation_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    task = str(args.get("task", "")).strip()
    if not task:
        return False
    run_claude_code_implementation(
        task,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


REGISTRY.register(
    ToolEntry(
        name="code_implement",
        description="Run code implementation workflow using Claude Code.",
        input_schema=object_schema(
            properties={
                "task": string_property(
                    description="Implementation task to execute in the codebase.",
                    min_length=1,
                )
            },
            required=("task",),
        ),
        execution_tier=ExecutionTier.ELEVATED,
        execute=execute_implementation_action,
    )
)
