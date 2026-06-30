"""MySQL Server Status Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_server_status,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


@tool(
    name="get_mysql_server_status",
    description="Retrieve MySQL server metrics including connections, uptime, query rates, and InnoDB buffer pool statistics.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking MySQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing InnoDB buffer pool hit ratio and deadlock counts",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
)
def get_mysql_server_status(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch server status metrics from a MySQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_server_status,
    )
