"""Supabase integration verifier."""

from __future__ import annotations

from typing import Any

from integrations.supabase import (
    SupabaseConfig,
    build_supabase_config,
    resolve_supabase_config,
    validate_supabase_config,
)
from integrations.verification import (
    register_verifier,
    verify_with_validation_result,
)


def _build_verified_config(config: dict[str, Any]) -> SupabaseConfig:
    """Build a config to verify from the resolved effective entry.

    ``resolve_effective_integrations`` publishes Supabase as ``project_url``
    only -- the service key never appears in a resolved config, by design, so
    ``build_supabase_config`` can't validate this shape directly. Re-resolve
    the full credentials from the store/env the same way the tools do.
    """
    project_url = str(config.get("project_url") or "").strip()
    if project_url:
        return resolve_supabase_config(project_url)
    return build_supabase_config(config)


@register_verifier("supabase")
def verify_supabase(source: str, config: dict[str, Any]) -> dict[str, str]:
    return verify_with_validation_result(
        "supabase",
        source,
        config,
        build_config=_build_verified_config,
        validate_config=validate_supabase_config,
    )
