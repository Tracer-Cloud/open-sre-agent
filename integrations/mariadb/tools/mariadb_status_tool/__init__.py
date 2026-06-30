"""MariaDB Global Status Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_global_status,
    mariadb_extract_params,
    mariadb_is_available,
)


@tool(
    name="get_mariadb_global_status",
    description="Retrieve key MariaDB server metrics including connections, threads, slow queries, InnoDB buffer pool stats, and uptime from SHOW GLOBAL STATUS.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
)
def get_mariadb_global_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch curated server metrics from SHOW GLOBAL STATUS."""

    def mariadb_config_builder(database: str) -> MariaDBConfig:
        return MariaDBConfig(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl=ssl,
        )

    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=mariadb_config_builder,
        resolver_kwargs={},
        db_caller=get_global_status,
    )
