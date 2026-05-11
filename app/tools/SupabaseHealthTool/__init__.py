"""Supabase Service Health Tool."""

from typing import Any

from app.integrations.supabase import (
    get_service_health,
    resolve_supabase_config,
    supabase_extract_params,
    supabase_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_supabase_service_health",
    description="Check the health of all Supabase services (PostgREST, Auth, Storage) for a given project.",
    source="supabase",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking Supabase project health during an incident",
        "Identifying which Supabase service (Auth, Storage, PostgREST) is degraded",
        "Triaging intermittent 503 or 401 errors from a Supabase-backed application",
    ],
    is_available=supabase_is_available,
    extract_params=supabase_extract_params,
)
def get_supabase_service_health(
    project_url: str,
) -> dict[str, Any]:
    """Fetch health status for all services in a Supabase project."""
    config = resolve_supabase_config(project_url)
    return get_service_health(config)
