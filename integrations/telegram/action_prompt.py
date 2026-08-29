"""Telegram action-agent prompt fragment — routes Telegram delivery requests to tools.

Registered with :func:`infrastructure.harness_providers.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def telegram_action_prompt_fragment() -> str:
    return """TELEGRAM DELIVERY:
- telegram_send_message — send a Telegram message ONLY when Telegram is connected
  and the user explicitly asks to send, post, notify, or message Telegram. Use the
  user's requested message body as `message`; do NOT use this for generic alerts
  or investigations unless the user specifically asks to send the result to Telegram.
Delivery tool unavailable for Telegram: do NOT invent a slash/CLI subcommand to
deliver a Telegram message and do NOT substitute a different channel. When
telegram_send_message is unavailable, explain that directly or route to
slash_invoke(command="/integrations", args=["setup", "telegram"])."""


__all__ = ["telegram_action_prompt_fragment"]
