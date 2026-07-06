"""Jira integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("jira")
def verify_jira(source: str, config: dict[str, Any]) -> dict[str, str]:
    base_url = str(config.get("base_url", "")).strip()
    email = str(config.get("email", "")).strip()
    api_token = str(config.get("api_token", "")).strip()
    if not base_url or not email or not api_token:
        return result("jira", source, "missing", "Missing base_url, email, or api_token.")
    return result("jira", source, "passed", f"Configured for Jira at {base_url.rstrip('/')}.")
