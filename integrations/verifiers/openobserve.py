"""OpenObserve integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("openobserve")
def verify_openobserve(source: str, config: dict[str, Any]) -> dict[str, str]:
    base_url = str(config.get("base_url", "")).strip()
    api_token = str(config.get("api_token", "")).strip()
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if not base_url:
        return result("openobserve", source, "missing", "Missing base_url.")
    if not (api_token or (username and password)):
        return result("openobserve", source, "missing", "Missing API token or username/password.")
    return result(
        "openobserve", source, "passed", f"Configured for OpenObserve at {base_url.rstrip('/')}."
    )
