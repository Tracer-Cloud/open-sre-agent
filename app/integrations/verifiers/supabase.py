"""Supabase integration verifier.

NOTE: arg names below preserve the existing pre-#37 behavior — the
first positional arg ends up in the ``service`` field of the result
dict and the literal ``"supabase"`` ends up in the ``source`` field.
This mirrors the original ``_verify_supabase`` exactly; do not "fix"
without a separate behavior-change PR.
"""

from __future__ import annotations

from typing import Any

from app.integrations.supabase import build_supabase_config, validate_supabase_config
from app.integrations.verification import (
    register_verifier,
    verify_with_validation_result,
)


@register_verifier("supabase")
def verify_supabase(service: str, config: dict[str, Any]) -> dict[str, str]:
    return verify_with_validation_result(
        service,
        "supabase",
        config,
        build_config=build_supabase_config,
        validate_config=validate_supabase_config,
    )
