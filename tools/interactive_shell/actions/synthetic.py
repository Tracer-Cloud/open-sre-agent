"""Synthetic test tool."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from tools.interactive_shell.contracts import (
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
    object_schema,
    string_property,
)
from tools.interactive_shell.synthetic.runner import (
    run_synthetic_test,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[2]


_RDS_POSTGRES_SUITE_DIR = _repo_root() / "tests" / "synthetic" / "rds_postgres"


@lru_cache(maxsize=1)
def list_rds_postgres_scenarios() -> tuple[str, ...]:
    """Enumerate available RDS Postgres synthetic scenario directory names."""
    if not _RDS_POSTGRES_SUITE_DIR.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in _RDS_POSTGRES_SUITE_DIR.iterdir()
            if entry.is_dir()
            and len(entry.name) >= 5
            and entry.name[:3].isdigit()
            and entry.name[3] == "-"
        )
    )


def execute_synthetic_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
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
    execute=execute_synthetic_tool,
    is_available=lambda session: capability_not_explicitly_disabled(session, "synthetic_suites"),
)


__all__ = ["TOOL_ENTRY", "execute_synthetic_tool", "list_rds_postgres_scenarios"]
