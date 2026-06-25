"""Supabase integration verifier.

KNOWN BUG, preserved on purpose: the first positional arg ends up in
the ``service`` field of the result dict and the literal ``"supabase"``
ends up in the ``source`` field — the two are swapped relative to every
other verifier. This mirrors the original ``_verify_supabase`` from the
pre-#3022 monolith. Downstream consumers that filter on
``result["service"] == "supabase"`` will not see Supabase rows; they
must filter on ``result["source"] == "supabase"`` instead.

TODO(opensre): file a tracking issue for the fix. Pinned by
``TestSupabasePreservedArgSwap`` in
``tests/integrations/test_verification_registry.py`` — that test must
be updated alongside any behavior change.
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
