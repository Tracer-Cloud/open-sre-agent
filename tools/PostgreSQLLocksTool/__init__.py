"""PostgreSQL Locks Tool."""

from typing import Any

from integrations.postgresql import (
    get_lock_status,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from tools.tool_decorator import tool
from tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


@tool(
    name="get_postgresql_lock_status",
    description=(
        "Retrieve active PostgreSQL locks and blocking relationships, including"
        " blocked queries, their blockers, and a summary of lock types."
    ),
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Diagnosing query blocking chains during performance incidents",
        "Identifying deadlock-prone transactions or long-held locks",
        "Investigating sudden latency spikes caused by lock contention",
    ],
    source_id="postgresql_pg_locks",
    evidence_type="query_stats",
    side_effect_level="read_only",
    examples=[
        "Check for blocked queries causing application timeouts.",
        "Find which query is blocking a deployment migration.",
    ],
    anti_examples=["Use this tool for disk usage or slow query history analysis."],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
)
def get_postgresql_lock_status(
    host: str,
    database: str | None = None,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch active lock and blocking chain information from a PostgreSQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_lock_status,
    )
