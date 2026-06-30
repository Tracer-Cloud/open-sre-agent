"""MariaDB Replication Status Tool."""

from typing import Any

from integrations.mariadb import (
    MariaDBConfig,
    get_replication_status,
    mariadb_extract_params,
    mariadb_is_available,
)
from tools.tool_decorator import tool
from tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


@tool(
    name="get_mariadb_replication_status",
    description="Retrieve MariaDB replication status including I/O and SQL thread state, lag, and errors from SHOW ALL SLAVES STATUS.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
)
def get_mariadb_replication_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch replication status from SHOW ALL SLAVES STATUS."""

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
        db_caller=get_replication_status,
    )
