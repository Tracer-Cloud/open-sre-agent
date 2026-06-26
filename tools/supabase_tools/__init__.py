# ======== from tools/supabase_health_tool/ ========

"""Supabase Service Health Tool."""

from typing import Any

from integrations.supabase import (
    get_service_health,
    resolve_supabase_config,
    supabase_extract_params,
    supabase_is_available,
)
from tools.tool_decorator import tool


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
    injected_params=("project_url",),
    extract_params=supabase_extract_params,
)
def get_supabase_service_health(
    project_url: str,
) -> dict[str, Any]:
    """Fetch health status for all services in a Supabase project."""
    config = resolve_supabase_config(project_url)
    return get_service_health(config)


# ======== from tools/supabase_storage_tool/ ========

"""Supabase Storage Buckets Tool."""

from typing import Any

from integrations.supabase import (
    get_storage_buckets,
    supabase_extract_params,
    supabase_is_available,
)
from tools.tool_decorator import tool


@tool(
    name="get_supabase_storage_buckets",
    description="List all Supabase Storage buckets and their configuration metadata.",
    source="supabase",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Auditing storage bucket configuration during a file upload incident",
        "Checking whether a bucket is public or private when debugging access errors",
        "Listing all buckets to identify orphaned or misconfigured storage resources",
    ],
    is_available=supabase_is_available,
    injected_params=("project_url",),
    extract_params=supabase_extract_params,
)
def get_supabase_storage_buckets(
    project_url: str,
) -> dict[str, Any]:
    """List all storage buckets in a Supabase project."""
    config = resolve_supabase_config(project_url)
    return get_storage_buckets(config)
