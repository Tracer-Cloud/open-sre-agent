"""Host capability policy for gateway chat sessions.

Gateway chat does not expose investigation / llm_provider / task_cancel tool
surfaces. Apply this when a session is prepared for a chat transport (resolver)
and again in the turn handler for tests that inject a ``SessionCore`` directly.
Assignments are idempotent — re-applying the same empty tuples is intentional.
"""

from __future__ import annotations

from typing import Any

UNSUPPORTED_GATEWAY_CAPABILITIES = (
    "investigation",
    "llm_provider",
    "task_cancel",
)


def ensure_gateway_capability_policy(session: Any) -> None:
    """Force unsupported capability keys to empty tool tuples on ``session``."""
    caps = getattr(session, "available_capabilities", None)
    if not isinstance(caps, dict):
        return
    for name in UNSUPPORTED_GATEWAY_CAPABILITIES:
        caps[name] = ()


__all__ = [
    "UNSUPPORTED_GATEWAY_CAPABILITIES",
    "ensure_gateway_capability_policy",
]
