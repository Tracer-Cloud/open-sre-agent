"""Supabase Storage Buckets Tool."""

from typing import Any

from app.integrations.supabase import (
    get_storage_buckets,
    resolve_supabase_config,
    supabase_extract_params,
    supabase_is_available,
)
from app.tools.tool_decorator import tool


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
    extract_params=supabase_extract_params,
)
def get_supabase_storage_buckets(
    project_url: str,
) -> dict[str, Any]:
    """List all storage buckets in a Supabase project."""
    config = resolve_supabase_config(project_url)
    return get_storage_buckets(config)
