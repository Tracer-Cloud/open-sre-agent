"""Mattermost action-agent prompt fragment — routes Mattermost delivery requests to tools.

Registered with :func:`platform.harness_ports.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def mattermost_action_prompt_fragment() -> str:
    return """MATTERMOST DELIVERY:
- mattermost_send_message — send a Mattermost message ONLY when Mattermost is
  connected and the user explicitly asks to send, post, notify, or message
  Mattermost. Use the user's requested message body as `message` and the named
  destination (channel id) as `channel`; with a webhook-only setup the
  destination is fixed, so omit `channel`.
Delivery tool unavailable for Mattermost: do NOT invent a slash/CLI subcommand
to deliver a Mattermost message and do NOT substitute a different channel.
When mattermost_send_message is unavailable, emit assistant_handoff or route to
slash_invoke(command="/integrations", args=["setup", "mattermost"])."""


__all__ = ["mattermost_action_prompt_fragment"]
