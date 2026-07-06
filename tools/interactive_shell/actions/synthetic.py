"""Synthetic test tool."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from config.constants.paths import SYNTHETIC_SCENARIOS_DIR
from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    capability_available_from_sources,
    execute_with_action_context,
    object_schema,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool
from tools.interactive_shell.synthetic.runner import (
    run_synthetic_test,
)


@lru_cache(maxsize=1)
def list_rds_postgres_scenarios() -> tuple[str, ...]:
    """Enumerate available RDS Postgres synthetic scenario directory names."""
    if not SYNTHETIC_SCENARIOS_DIR.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in SYNTHETIC_SCENARIOS_DIR.iterdir()
            if entry.is_dir()
            and len(entry.name) >= 5
            and entry.name[:3].isdigit()
            and entry.name[3] == "-"
        )
    )


def execute_synthetic_tool(args: dict[str, Any], ctx: ActionToolContext) -> bool:
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


def run_synthetic(*, suite: str, scenario: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"suite": suite, "scenario": scenario},
        context,
        execute_synthetic_tool,
    )


synthetic_run_tool = RegisteredTool(
    name="synthetic_run",
    description=(
        "Run a synthetic scenario in a suite. Match the scenario id exactly from "
        "the user request: a bare numeric prefix selects the enum value with that "
        'same prefix, e.g. "005" -> "005-failover" and "004" -> '
        '"004-cpu-saturation-bad-query". Never substitute a neighboring numbered '
        "scenario when the user supplied a numeric id."
    ),
    input_schema=object_schema(
        properties={
            "suite": string_property(
                description="Synthetic suite name.",
                enum=("rds_postgres",),
            ),
            "scenario": string_property(
                description=(
                    "Synthetic scenario id within the selected suite or `all`. "
                    "For bare numeric requests, use the enum value with the same "
                    "three-digit prefix."
                ),
                enum=("all", *list_rds_postgres_scenarios()),
            ),
        },
        required=("suite", "scenario"),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_synthetic,
    is_available=lambda sources: capability_available_from_sources(sources, "synthetic_suites"),
)


__all__ = ["execute_synthetic_tool", "list_rds_postgres_scenarios", "synthetic_run_tool"]
