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


def _build_config_resolving_credentials(config: dict[str, Any]) -> SupabaseConfig:
    """Build a full config from either credential shape.

    The effective resolution deliberately publishes only ``project_url`` —
    the service key never appears in resolved configs — so verification
    re-resolves credentials from the store or environment the same way the
    tools do. A config that already carries ``url``/``service_key`` builds
    directly.
    """
    project_url = str(config.get("project_url", "")).strip()
    if project_url and not (config.get("url") or config.get("service_key")):
        return resolve_supabase_config(project_url)
    return build_supabase_config(config)


@register_verifier("supabase")
def verify_supabase(source: str, config: dict[str, Any]) -> dict[str, str]:
    return verify_with_validation_result(
        "supabase",
        source,
        config,
        build_config=_build_config_resolving_credentials,
        validate_config=validate_supabase_config,
    )
