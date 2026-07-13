"""Inbound authorization helpers for the messaging gateway."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from integrations.messaging_security import (
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    audit_log_inbound_message,
    authorize_inbound_message,
    complete_pairing,
)
from integrations.store import get_integration, upsert_instance


@dataclass(frozen=True)
class InboundDecision:
    """Authorization outcome for one inbound Telegram message."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


def _load_policy() -> tuple[dict | None, MessagingIdentityPolicy]:
    record = get_integration(MessagingPlatform.TELEGRAM.value)
    if record is None:
        return None, MessagingIdentityPolicy(inbound_enabled=True)
    credentials = record.get("credentials", {})
    raw_policy = credentials.get("identity_policy")
    if raw_policy and isinstance(raw_policy, dict):
        return record, MessagingIdentityPolicy.model_validate(raw_policy)
    return record, MessagingIdentityPolicy(inbound_enabled=True)


def _save_policy(record: dict | None, policy: MessagingIdentityPolicy) -> None:
    instances = record.get("instances", []) if record else []
    first_instance = instances[0] if instances else {}
    instance_name = (
        first_instance.get("name", "default") if isinstance(first_instance, dict) else "default"
    )
    credentials = dict(record.get("credentials", {})) if record else {}
    credentials["identity_policy"] = policy.model_dump(mode="json")
    upsert_instance(
        MessagingPlatform.TELEGRAM.value,
        {
            "name": instance_name,
            "tags": first_instance.get("tags", {}) if isinstance(first_instance, dict) else {},
            "credentials": credentials,
        },
        record_id=record.get("id") if record else None,
    )


def _message_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def enforce_inbound_telegram_message_security(
    *,
    user_id: str,
    chat_id: str,
    text: str,
    env_allowed_user_ids: list[str],
) -> InboundDecision:
    """Authorize inbound Telegram DM text and handle /pair attempts."""
    record, policy = _load_policy()
    if env_allowed_user_ids and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_user_ids)
        policy.inbound_enabled = True

    if text.strip().lower().startswith("/pair "):
        code = text.strip().split(maxsplit=1)[1] if " " in text.strip() else ""
        ok, msg = complete_pairing(policy=policy, user_id=user_id, code=code)
        audit_log_inbound_message(
            platform=MessagingPlatform.TELEGRAM.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=_message_hash(text),
            authorized=ok,
            reason=msg,
        )
        return InboundDecision(
            allowed=False,
            reply_text=msg,
            persist_policy=True,
            updated_policy=policy,
        )

    if text.strip().lower() in {"/start", "/help"}:
        audit_log_inbound_message(
            platform=MessagingPlatform.TELEGRAM.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=_message_hash(text),
            authorized=True,
            reason="builtin command",
        )
        return InboundDecision(
            allowed=False,
            reply_text=(
                "OpenSRE Telegram gateway (DM text only).\n"
                "Send a message to chat with the agent.\n"
                "Commands: /new (new session), /help"
            ),
        )

    result: AuthorizationResult = authorize_inbound_message(
        policy=policy,
        user_id=user_id,
        chat_id=chat_id,
        message_text=text,
    )

    if text.strip().lower() == "/new":
        if not result:
            audit_log_inbound_message(
                platform=MessagingPlatform.TELEGRAM.value,
                user_id=user_id,
                chat_id=chat_id,
                message_hash=_message_hash(text),
                authorized=False,
                reason=result.reason,
            )
            return InboundDecision(allowed=False, reply_text=result.reason)
        audit_log_inbound_message(
            platform=MessagingPlatform.TELEGRAM.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=_message_hash(text),
            authorized=True,
            reason="session rotate",
        )
        return InboundDecision(allowed=True, reply_text="__ROTATE_SESSION__")

    audit_log_inbound_message(
        platform=MessagingPlatform.TELEGRAM.value,
        user_id=user_id,
        chat_id=chat_id,
        message_hash=_message_hash(text),
        authorized=bool(result),
        reason=result.reason,
    )
    if result:
        return InboundDecision(allowed=True)
    return InboundDecision(allowed=False, reply_text=result.reason)


def persist_policy_if_needed(decision: InboundDecision) -> None:
    if not decision.persist_policy or decision.updated_policy is None:
        return
    record, _ = _load_policy()
    _save_policy(record, decision.updated_policy)
