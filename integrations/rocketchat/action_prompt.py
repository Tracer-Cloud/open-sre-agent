"""Rocket.Chat action-agent prompt fragment — routes Rocket.Chat delivery requests to tools.

Registered with :func:`infrastructure.harness_providers.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def rocketchat_action_prompt_fragment() -> str:
    return """ROCKET.CHAT DELIVERY:
- rocketchat_send_message — send a Rocket.Chat message ONLY when Rocket.Chat is
  connected and the user explicitly asks to send, post, notify, or message
  Rocket.Chat. Use the user's requested message body as `message` and the named
  destination (#channel / @user) as `channel`; with a webhook-only setup the
  destination is fixed, so omit `channel`.
Delivery tool unavailable for Rocket.Chat: do NOT invent a slash/CLI subcommand
to deliver a Rocket.Chat message and do NOT substitute a different channel.
When rocketchat_send_message is unavailable, explain that directly or route to
slash_invoke(command="/integrations", args=["setup", "rocketchat"])."""


__all__ = ["rocketchat_action_prompt_fragment"]
