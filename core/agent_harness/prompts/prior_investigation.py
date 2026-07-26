"""Prior-investigation facts shared by the gather and assistant prompts.

Leaf module: both ``gather`` and ``assistant`` import these headline facts, so
the wording stays identical between the turn's two prompts without either
module importing the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def prior_investigation_headline(state: Mapping[str, Any]) -> list[str]:
    """Return the alert name and root cause lines present in ``state``."""
    parts: list[str] = []
    alert_name = state.get("alert_name")
    if alert_name:
        parts.append(f"Alert: {alert_name}")
    root_cause = state.get("root_cause")
    if root_cause:
        parts.append(f"Root cause: {root_cause}")
    return parts


__all__ = ["prior_investigation_headline"]
