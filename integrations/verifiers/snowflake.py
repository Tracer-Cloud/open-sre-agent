"""Snowflake integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("snowflake")
def verify_snowflake(source: str, config: dict[str, Any]) -> dict[str, str]:
    account_identifier = str(config.get("account_identifier", "")).strip()
    token = str(config.get("token", "")).strip()
    if not account_identifier:
        return result("snowflake", source, "missing", "Missing account_identifier.")
    if not token:
        return result("snowflake", source, "missing", "Missing token credentials.")
    return result(
        "snowflake", source, "passed", f"Configured for Snowflake account {account_identifier}."
    )
