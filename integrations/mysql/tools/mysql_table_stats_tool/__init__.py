"""MySQL Table Stats Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_table_stats,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


@tool(
    name="get_mysql_table_stats",
    description="Retrieve MySQL table statistics including row counts and data/index sizes from information_schema.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying the largest tables consuming storage during capacity incidents",
        "Reviewing table sizes and growth patterns for capacity planning",
        "Finding tables with unexpectedly high row counts or index overhead",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
)
def get_mysql_table_stats(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch table statistics for all base tables in the target database."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_table_stats,
    )
