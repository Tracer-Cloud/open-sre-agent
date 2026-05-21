"""Synthetic test action tool."""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_synthetic_test,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    list_rds_postgres_scenarios,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
    object_schema,
    string_property,
)


def execute_synthetic_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    suite = str(args.get("suite", "")).strip()
    scenario = str(args.get("scenario", "")).strip()
    if not suite or not scenario:
        return False
    run_synthetic_test(
        f"{suite}:{scenario}",
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


TOOL_ENTRY = ToolEntry(
    name="synthetic_run",
    description="Run a synthetic scenario in a suite.",
    input_schema=object_schema(
        properties={
            "suite": string_property(
                description="Synthetic suite name.",
                enum=("rds_postgres",),
            ),
            "scenario": string_property(
                description="Synthetic scenario id within the selected suite or `all`.",
                enum=("all", *list_rds_postgres_scenarios()),
            ),
        },
        required=("suite", "scenario"),
    ),
    execution_tier=ExecutionTier.ELEVATED,
    execute=execute_synthetic_action,
    is_available=lambda session: capability_not_explicitly_disabled(session, "synthetic_suites"),
)


__all__ = ["TOOL_ENTRY", "execute_synthetic_action"]
