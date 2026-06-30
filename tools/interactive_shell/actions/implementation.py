"""Implementation tool."""

from __future__ import annotations

from typing import Any

from tools.interactive_shell.contracts import (
    ToolContext,
    capability_available_from_sources,
    execute_with_repl_context,
    object_schema,
    string_property,
)
from tools.interactive_shell.implementation.claude_code_executor import (
    run_claude_code_implementation,
)
from tools.registered_tool import RegisteredTool


def execute_implementation_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
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


def run_implementation(*, task: str, context: Any) -> dict[str, Any]:
    return execute_with_repl_context({"task": task}, context, execute_implementation_tool)


code_implement_tool = RegisteredTool(
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
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_implementation,
    is_available=lambda sources: capability_available_from_sources(sources, "implementation"),
)


__all__ = ["code_implement_tool", "execute_implementation_tool"]
