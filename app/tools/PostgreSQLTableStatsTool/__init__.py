"""PostgreSQL Table Stats Tool."""

from typing import Any

from app.integrations.postgresql import (
    get_table_stats,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


@tool(
    name="get_postgresql_table_stats",
    description="Retrieve PostgreSQL table statistics including size, row counts, index usage, and maintenance info.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying large tables or rapid table growth during storage incidents",
        "Analyzing table scan patterns and index usage efficiency",
        "Checking table maintenance status like vacuum and analyze operations",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
)
def get_postgresql_table_stats(
    host: str,
    database: str | None = None,
    schema_name: str = "public",
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch table statistics for a specific schema (default 'public')."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_table_stats(config, schema_name=schema_name),
    )
