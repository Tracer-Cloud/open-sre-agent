"""OpenSearch integration verifier — config presence + basic-auth completeness.

Basic auth is all-or-nothing: ``ElasticsearchConfig`` silently drops the
Authorization header when either half is missing, so a username without a
password (or the reverse) would send unauthenticated requests against a secured
cluster. Rejecting the half-filled pair here — rather than in a setup-only hook —
keeps setup and the ``integrations verify`` health check agreeing on what
"configured" means.
"""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("opensearch")
def verify_opensearch(source: str, config: dict[str, Any]) -> dict[str, str]:
    url = str(config.get("url", "")).strip()
    if not url:
        return result("opensearch", source, "missing", "Missing url.")
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if bool(username) != bool(password):
        return result(
            "opensearch",
            source,
            "failed",
            "Provide both username and password for basic auth, or leave both blank.",
        )
    return result(
        "opensearch", source, "passed", f"Configured for OpenSearch at {url.rstrip('/')}."
    )
