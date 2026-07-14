"""Inbound authorization helpers for the Slack Socket Mode gateway.

Mirrors ``gateway.telegram.inbound_security``: load/persist ``identity_policy``
from the integration store, honor ``opensre messaging allow/pair``, and handle
``/pair``, ``/new``, and ``/help`` before agent turns.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.messaging_security import (
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    audit_log_inbound_message,
    authorize_inbound_message,
    complete_pairing,
    message_hash,
)
from integrations.store import get_integration, upsert_instance

_PLATFORM = MessagingPlatform.SLACK.value
_ROTATE_SESSION = "__ROTATE_SESSION__"
_HELP_TEXT = (
    "OpenSRE Slack gateway.\n"
    "Mention the bot or DM it to chat with the agent.\n"
    "Commands: /new (new session), /help, /pair <code>"
)
_EMPTY_ALLOWLIST_REASON = (
    "Slack allowlist is empty; run `opensre messaging allow -p slack -u <id>`, "
    "set SLACK_ALLOWED_USERS, or set SLACK_ALLOW_OPEN_WORKSPACE=1"
)


@dataclass(frozen=True)
class SlackInboundDecision:
    """Authorization outcome for one inbound Slack message."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


def _load_policy() -> tuple[dict | None, MessagingIdentityPolicy]:
    record = get_integration(_PLATFORM)
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
        _PLATFORM,
        {
            "name": instance_name,
            "tags": first_instance.get("tags", {}) if isinstance(first_instance, dict) else {},
            "credentials": credentials,
        },
        record_id=record.get("id") if record else None,
    )


def _audit(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    authorized: bool,
    reason: str,
) -> None:
    audit_log_inbound_message(
        platform=_PLATFORM,
        user_id=user_id,
        chat_id=channel_id,
        message_hash=message_hash(text),
        authorized=authorized,
        reason=reason,
    )


def _merge_env_allowlist(
    policy: MessagingIdentityPolicy,
    env_allowed_user_ids: list[str],
) -> MessagingIdentityPolicy:
    if env_allowed_user_ids and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_user_ids)
        policy.inbound_enabled = True
    return policy


def _handle_pair_command(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    stripped: str,
    policy: MessagingIdentityPolicy,
) -> SlackInboundDecision:
    code = stripped.split(maxsplit=1)[1] if " " in stripped else ""
    ok, msg = complete_pairing(policy=policy, user_id=user_id, code=code)
    _audit(user_id=user_id, channel_id=channel_id, text=text, authorized=ok, reason=msg)
    return SlackInboundDecision(
        allowed=False,
        reply_text=msg,
        persist_policy=True,
        updated_policy=policy,
    )


def _authorize_sender(
    *,
    policy: MessagingIdentityPolicy,
    user_id: str,
    channel_id: str,
    text: str,
    allow_open_workspace: bool,
) -> AuthorizationResult | None:
    """Return auth result, or ``None`` when the allowlist is empty and closed."""
    if not policy.allowed_user_ids and not allow_open_workspace:
        return None
    if allow_open_workspace and not policy.allowed_user_ids:
        return AuthorizationResult(allowed=True, reason="open workspace")
    return authorize_inbound_message(
        policy=policy,
        user_id=user_id,
        chat_id=channel_id,
        message_text=text,
    )


def _decision_after_auth(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    lower: str,
    result: AuthorizationResult,
) -> SlackInboundDecision:
    if lower == "/new":
        if not result:
            _audit(
                user_id=user_id,
                channel_id=channel_id,
                text=text,
                authorized=False,
                reason=result.reason,
            )
            return SlackInboundDecision(allowed=False)
        _audit(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            authorized=True,
            reason="session rotate",
        )
        return SlackInboundDecision(allowed=True, reply_text=_ROTATE_SESSION)

    _audit(
        user_id=user_id,
        channel_id=channel_id,
        text=text,
        authorized=bool(result),
        reason=result.reason,
    )
    return SlackInboundDecision(allowed=bool(result))


def enforce_inbound_slack_message_security(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    env_allowed_user_ids: list[str],
    allow_open_workspace: bool = False,
) -> SlackInboundDecision:
    """Authorize inbound Slack text and handle /pair, /new, /help."""
    _record, policy = _load_policy()
    policy = _merge_env_allowlist(policy, env_allowed_user_ids)

    stripped = text.strip()
    lower = stripped.lower()

    if lower.startswith("/pair "):
        return _handle_pair_command(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            stripped=stripped,
            policy=policy,
        )

    if lower in {"/help", "/start"}:
        _audit(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            authorized=True,
            reason="builtin command",
        )
        return SlackInboundDecision(allowed=False, reply_text=_HELP_TEXT)

    result = _authorize_sender(
        policy=policy,
        user_id=user_id,
        channel_id=channel_id,
        text=text,
        allow_open_workspace=allow_open_workspace,
    )
    if result is None:
        _audit(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            authorized=False,
            reason=_EMPTY_ALLOWLIST_REASON,
        )
        return SlackInboundDecision(allowed=False)

    return _decision_after_auth(
        user_id=user_id,
        channel_id=channel_id,
        text=text,
        lower=lower,
        result=result,
    )


def persist_policy_if_needed(decision: SlackInboundDecision) -> None:
    if not decision.persist_policy or decision.updated_policy is None:
        return
    record, _ = _load_policy()
    _save_policy(record, decision.updated_policy)
