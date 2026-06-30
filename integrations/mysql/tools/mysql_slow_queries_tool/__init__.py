"""MySQL Slow Queries Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_slow_queries,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


@tool(
    name="get_mysql_slow_queries",
    description="Retrieve slow MySQL queries from performance_schema, ranked by average execution time.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying slow queries that may be causing performance degradation",
        "Analyzing query execution patterns during incident timeframes",
        "Finding poorly optimized queries with high execution times or full-table scans",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
)
def get_mysql_slow_queries(
    host: str,
    database: str | None = None,
    threshold_ms: float = 1000.0,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch slow query statistics above threshold_ms mean execution time (default 1000ms)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_slow_queries(config, threshold_ms=threshold_ms),
    )
