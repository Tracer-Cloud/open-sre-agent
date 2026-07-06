"""Prefect integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("prefect")
def verify_prefect(source: str, config: dict[str, Any]) -> dict[str, str]:
    api_url = str(config.get("api_url", "")).strip()
    api_key = str(config.get("api_key", "")).strip()
    if not api_url and not api_key:
        return result("prefect", source, "missing", "Missing api_url or api_key.")
    return result("prefect", source, "passed", f"Configured for Prefect at {api_url or 'cloud'}.")
