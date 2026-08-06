"""Buzz action-agent prompt fragment — routes Buzz delivery requests to tools.

Registered with :func:`platform.harness_ports.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def buzz_action_prompt_fragment() -> str:
    return """BUZZ DELIVERY:
- buzz_send_message — send a Buzz message ONLY when Buzz is connected and the
  user explicitly asks to send, post, notify, or message Buzz. Use the user's
  requested message body as `message` and the named channel UUID as `channel`;
  omit `channel` to use the configured default_channel.
Delivery tool unavailable for Buzz: do NOT invent a slash/CLI subcommand to
deliver a Buzz message and do NOT substitute a different channel. When
buzz_send_message is unavailable, emit assistant_handoff or route to
slash_invoke(command="/integrations", args=["setup", "buzz"])."""


__all__ = ["buzz_action_prompt_fragment"]
