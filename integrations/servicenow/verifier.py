"""ServiceNow integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("servicenow")
def verify_servicenow(source: str, config: dict[str, Any]) -> dict[str, str]:
    instance_url = str(config.get("instance_url", "")).strip()
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if not instance_url or not username or not password:
        return result(
            "servicenow", source, "missing", "Missing instance_url, username, or password."
        )
    return result(
        "servicenow",
        source,
        "passed",
        f"Configured for ServiceNow at {instance_url.rstrip('/')}.",
    )
