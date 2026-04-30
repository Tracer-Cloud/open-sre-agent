"""Shared service metadata for integration resolution."""

from __future__ import annotations


def should_publish_instance_siblings(instances: object) -> bool:
    """Return whether an effective integration should expose ``instances``."""
    if not isinstance(instances, list) or not instances:
        return False
    if len(instances) > 1:
        return True
    return str(instances[0].get("name", "default")) != "default"
