"""Per-turn metadata injected into gateway session integration caches."""

from __future__ import annotations

from typing import Any

from integrations.messaging_security import MessagingPlatform


def _inactive_chat_transports(active_platform: str) -> tuple[str, ...]:
    """Chat transports other than the active one, so the agent hides them.

    Transport knowledge lives here in the gateway (which owns messaging), not in
    core: a Slack teammate should not advertise Telegram, and vice versa.
    """
    active = active_platform.strip().lower()
    return tuple(p.value for p in MessagingPlatform if p.value != active)


def inject_gateway_chat_context(
    resolved: dict[str, Any], chat_id: str, platform: str = ""
) -> dict[str, Any]:
    merged = dict(resolved)
    merged["_gateway_chat_id"] = chat_id
    if platform:
        merged["_gateway_platform"] = platform
        merged["_gateway_hidden_integrations"] = _inactive_chat_transports(platform)
    return merged
