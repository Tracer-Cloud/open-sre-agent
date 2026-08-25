"""MariaDB Slow Queries Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_slow_queries,
    mariadb_extract_params,
    mariadb_is_available,
)


def _map_get_mariadb_slow_queries(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    queries = output.get("queries", [])
    if queries:
        record_evidence_entry(
            evidence,
            source="get_mariadb_slow_queries",
            label="MariaDB Slow Queries",
            summary=f"{len(queries)} slow queries",
        )


@tool(
    name="get_mariadb_slow_queries",
    description="Retrieve top MariaDB queries by average execution time from performance_schema.events_statements_summary_by_digest.",
    source="mariadb",
    evidence_mapper=_map_get_mariadb_slow_queries,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
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
