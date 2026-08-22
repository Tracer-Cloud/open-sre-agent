"""Availability check for GitHub integration"""

from __future__ import annotations


def github_source_available(sources: dict[str, dict]) -> bool:
    """Return True when the GitHub integration is configured and reachable.

    ``sources`` is the per-integration view assembled by the runtime from the
    integration store; the relevant entry is ``sources["github"]``. Returns
    True only when that entry's ``connection_verified`` flag is set truthy
    (typically by the verifier after a live credentials check). Missing
    ``github`` entry or a falsy/missing ``connection_verified`` returns False.
    """
    return bool(sources.get("github", {}).get("connection_verified"))
