"""OpenSearch integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from app.integrations.verification import register_verifier, result


@register_verifier("opensearch")
def verify_opensearch(source: str, config: dict[str, Any]) -> dict[str, str]:
    url = str(config.get("url", "")).strip()
    if not url:
        return result("opensearch", source, "missing", "Missing url.")
    return result(
        "opensearch", source, "passed", f"Configured for OpenSearch at {url.rstrip('/')}."
    )
