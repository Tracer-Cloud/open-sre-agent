"""Gateway configuration loaded from environment and integration store."""

from __future__ import annotations

import os
from dataclasses import dataclass

from config.strict_config import StrictConfigModel
from integrations.messaging_security import MessagingIdentityPolicy, MessagingPlatform
from integrations.store import get_integration


class GatewaySettings(StrictConfigModel):
    """Runtime settings for the Telegram gateway process."""

    bot_token: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_port: int = 8443
    host: str = "127.0.0.1"
    allowed_user_ids: list[str] = []
    max_concurrent_turns: int = 4
    approval_timeout_seconds: int = 600
    gate_side_effects: bool = True
    stream_edit_interval_seconds: float = 1.5


@dataclass(frozen=True)
class TelegramInboundMessage:
    """Normalized inbound Telegram DM text."""

    update_id: int
    user_id: str
    chat_id: str
    message_id: str
    text: str
    callback_query_id: str = ""
    callback_data: str = ""


def _parse_csv_ids(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_identity_policy() -> MessagingIdentityPolicy:
    """Load Telegram identity policy from the integration store."""
    record = get_integration(MessagingPlatform.TELEGRAM.value)
    if record is None:
        return MessagingIdentityPolicy()
    credentials = record.get("credentials", {})
    raw_policy = credentials.get("identity_policy")
    if raw_policy and isinstance(raw_policy, dict):
        return MessagingIdentityPolicy.model_validate(raw_policy)
    return MessagingIdentityPolicy()


def load_gateway_settings() -> GatewaySettings:
    """Build gateway settings from env vars with integration-store fallback."""
    policy = load_identity_policy()
    env_allowed = _parse_csv_ids(os.environ.get("TELEGRAM_ALLOWED_USERS", ""))
    allowed = policy.allowed_user_ids or env_allowed
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        record = get_integration(MessagingPlatform.TELEGRAM.value)
        if record:
            creds = record.get("credentials", {})
            token = str(creds.get("bot_token") or "").strip()

    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if webhook_url and not webhook_secret:
        msg = "TELEGRAM_WEBHOOK_SECRET is required when TELEGRAM_WEBHOOK_URL is set"
        raise ValueError(msg)

    port_raw = os.environ.get("TELEGRAM_WEBHOOK_PORT", "8443").strip()
    host = os.environ.get("TELEGRAM_GATEWAY_HOST", "127.0.0.1").strip() or "127.0.0.1"

    return GatewaySettings(
        bot_token=token,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        webhook_port=int(port_raw),
        host=host,
        allowed_user_ids=allowed,
        max_concurrent_turns=int(os.environ.get("TELEGRAM_GATEWAY_MAX_CONCURRENT", "4")),
        approval_timeout_seconds=int(os.environ.get("TELEGRAM_GATEWAY_APPROVAL_TIMEOUT", "600")),
        gate_side_effects=os.environ.get("TELEGRAM_GATEWAY_GATE_SIDE_EFFECTS", "true").lower()
        not in {"0", "false", "no"},
    )
