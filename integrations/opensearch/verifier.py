"""OpenSearch integration verifier — config presence + auth-method coherence.

Two auth rules are enforced here rather than in a setup-only hook, so setup and
the ``integrations verify`` health check agree on what "configured" means:

- API key **and** basic auth are mutually exclusive. ``ElasticsearchConfig``
  prioritizes the API key when both are present, so a stale key would silently
  override valid basic credentials — verification would pass while runtime
  authenticated with the wrong method. Reject the combination.
- Basic auth is all-or-nothing: ``ElasticsearchConfig`` drops the Authorization
  header when either half is missing, so a username without a password (or the
  reverse) would send unauthenticated requests against a secured cluster.
"""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("opensearch")
def verify_opensearch(source: str, config: dict[str, Any]) -> dict[str, str]:
    url = str(config.get("url", "")).strip()
    if not url:
        return result("opensearch", source, "missing", "Missing url.")
    api_key = str(config.get("api_key", "")).strip()
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if api_key and (username or password):
        return result(
            "opensearch",
            source,
            "failed",
            "Use an API key or basic auth, not both — runtime would ignore the basic credentials.",
        )
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
