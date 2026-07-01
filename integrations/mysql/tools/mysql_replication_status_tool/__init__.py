"""MySQL Replication Status Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_replication_status,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


@tool(
    name="get_mysql_replication_status",
    description="Retrieve MySQL replication status including IO/SQL thread health and replica lag.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking replica lag during high-write incidents",
        "Verifying replication IO and SQL threads are running",
        "Diagnosing replication errors and identifying last error details",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
)
def get_mysql_replication_status(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch replication status from a MySQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_replication_status,
    )
