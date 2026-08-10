"""GCP-wide availability check shared by every GCP tool.

Canonical home for any availability helper used by more than one GCP tool,
mirroring ``integrations/aws/availability.py``.
"""

from __future__ import annotations


def gcp_available(sources: dict[str, dict]) -> bool:
    """Available once a project is resolved.

    Credentials are intentionally not probed here: ``is_available`` runs during
    tool planning for every turn, and Application Default Credentials on GKE
    resolve through the node metadata server. Probing would put a network call
    on the planning path to answer a question the first real call answers
    anyway.
    """
    return bool(sources.get("gcp", {}).get("project_id"))
