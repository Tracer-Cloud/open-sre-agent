"""Resolve the owning principal for a Telegram gateway turn.

Telegram DMs attribute data to the silo organization (``ORGANIZATION_ID``) and
key per-actor sessions by Telegram user id — same border as Slack/Discord org +
actor. Must not import Slack/Discord principal modules (peer isolation).
"""

from __future__ import annotations

from config.principal import Actor, Principal, StorageScope
from gateway.core.billing.credits_client import organization_id_for_silo


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Telegram turn's data cannot be established."""


def resolve_telegram_principal() -> Principal:
    """Principal for a Telegram turn: the organization this deployment serves."""
    silo_org = organization_id_for_silo()
    if not silo_org:
        raise PrincipalResolutionError(
            "Telegram turn cannot be resolved: no organization is configured for this deployment"
        )
    return Principal.org(silo_org)


def telegram_scope(principal: Principal, user_id: str) -> StorageScope:
    """Pair an organization with the Telegram user acting in it."""
    return StorageScope(principal=principal, actor=Actor(id=user_id))


def resolve_telegram_scope(*, user_id: str) -> StorageScope:
    """Owning principal and acting member for one Telegram turn."""
    user = (user_id or "").strip()
    if not user:
        raise PrincipalResolutionError("Telegram turn carried no user id")
    return telegram_scope(resolve_telegram_principal(), user)


__all__ = [
    "PrincipalResolutionError",
    "resolve_telegram_principal",
    "resolve_telegram_scope",
    "telegram_scope",
]
