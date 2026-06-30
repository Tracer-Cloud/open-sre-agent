"""MariaDB Slow Queries Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_slow_queries,
    mariadb_extract_params,
    mariadb_is_available,
)


@tool(
    name="get_mariadb_slow_queries",
    description="Retrieve top MariaDB queries by average execution time from performance_schema.events_statements_summary_by_digest.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
)
def get_mariadb_slow_queries(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch slow queries from performance_schema."""

    def mariadb_config_builder(database: str) -> MariaDBConfig:
        return MariaDBConfig(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl=ssl,
            max_results=max_results,
        )

    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=mariadb_config_builder,
        resolver_kwargs={},
        db_caller=get_slow_queries,
    )
