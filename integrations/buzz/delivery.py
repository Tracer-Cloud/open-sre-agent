"""Buzz delivery helper - posts investigation findings via ``buzz-cli``."""

from __future__ import annotations

import logging
from typing import Any

from integrations.buzz.client import BuzzClient
from integrations.config_models import BuzzConfig
from platform.common.truncation import truncate
from platform.notifications.limits import MAX_MESSAGE_SIZE
from platform.notifications.redaction import redact_token

logger = logging.getLogger(__name__)

_REPORT_TEXT_LIMIT = MAX_MESSAGE_SIZE


def post_buzz_message(
    relay_url: str,
    channel: str,
    text: str,
    private_key: str,
    *,
    auth_tag: str = "",
    buzz_path: str = "buzz",
    reply_to: str = "",
) -> tuple[bool, str, str]:
    """Send a message via ``buzz messages send``.

    Returns ``(ok, error, event_id)``. ``ok`` is False on expected failures
    (missing binary, relay unreachable, auth rejected, ...).
    """
    config = BuzzConfig(
        relay_url=relay_url,
        private_key=private_key,
        auth_tag=auth_tag,
        buzz_path=buzz_path or "buzz",
    )
    result = BuzzClient(config).send_message(channel=channel, content=text, reply_to=reply_to)
    if not result["success"]:
        safe_error = redact_token(str(result["error"]), private_key)
        logger.warning("[buzz] send message failed: %s", safe_error)
        return False, safe_error, ""
    return True, "", str(result["event_id"])


def send_buzz_report(report: str, buzz_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Deliver an investigation report to the configured Buzz channel."""
    relay_url = str(buzz_ctx.get("relay_url") or "http://localhost:3000")
    channel = str(buzz_ctx.get("channel") or "")
    private_key = str(buzz_ctx.get("private_key") or "")
    auth_tag = str(buzz_ctx.get("auth_tag") or "")
    buzz_path = str(buzz_ctx.get("buzz_path") or "buzz")

    text = truncate(f"OpenSRE Investigation\n\n{report}", _REPORT_TEXT_LIMIT, suffix="…")
    ok, error, _event_id = post_buzz_message(
        relay_url,
        channel,
        text,
        private_key,
        auth_tag=auth_tag,
        buzz_path=buzz_path,
    )
    return (True, "") if ok else (False, error)


__all__ = ["post_buzz_message", "send_buzz_report"]
