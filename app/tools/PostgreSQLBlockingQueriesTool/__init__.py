"""PostgreSQL Blocking Queries Tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.integrations.postgresql import (
    get_blocking_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


class PostgreSQLBlockingQueriesInput(BaseModel):
    host: str = Field(description="PostgreSQL host or endpoint name.")
    database: str | None = Field(
        default=None,
        description="Target database name. Defaults to integration database when omitted.",
    )
    port: int = Field(default=5432, description="PostgreSQL TCP port.")


class PostgreSQLBlockingQueriesOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether queries were retrieved.")
    queries: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Blocked and blocking queries to identify lock contention.",
    )
    total_queries: int = Field(default=0, description="Number of query rows returned.")
    database: str | None = Field(default=None, description="Database queried.")
    default_db_warning: str | None = Field(
        default=None,
        description="Warning emitted when the default database fallback is used.",
    )
    error: str | None = Field(default=None, description="Error details when query fails.")


@tool(
    name="get_postgresql_blocking_queries",
    description=(
        "Retrieve currently blocked and blocking queries in PostgreSQL to diagnose lock contention."
    ),
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Diagnosing lock contention, deadlocks, or blocked transactions",
        "Identifying long-running queries holding database locks",
        "Correlating high database wait times with blocking PIDs",
    ],
    source_id="postgresql_blocking_queries",
    evidence_type="query_stats",
    side_effect_level="read_only",
    examples=[
        "Identify blocking queries to resolve lock contention.",
    ],
    anti_examples=[
        "Use this tool for CPU spike investigations not relating to database lock timeouts."
    ],
    input_model=PostgreSQLBlockingQueriesInput,
    output_model=PostgreSQLBlockingQueriesOutput,
    is_available=postgresql_is_available,
    extract_params=postgresql_extract_params,
)
def get_postgresql_blocking_queries(
    host: str,
    database: str | None = None,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch blocked and blocking queries in PostgreSQL."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_blocking_queries,
    )
