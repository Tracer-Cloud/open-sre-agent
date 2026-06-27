"""Supabase integration verifier."""

from __future__ import annotations

from typing import Any

from integrations.supabase import build_supabase_config, validate_supabase_config
from integrations.verification import (
    register_verifier,
    verify_with_validation_result,
)


@register_verifier("supabase")
def verify_supabase(source: str, config: dict[str, Any]) -> dict[str, str]:
    return verify_with_validation_result(
        "supabase",
        source,
        config,
        build_config=build_supabase_config,
        validate_config=validate_supabase_config,
    )
