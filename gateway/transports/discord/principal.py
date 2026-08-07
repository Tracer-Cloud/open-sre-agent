"""Resolve the owning principal for a Discord gateway turn.

Discord guild/DM turns attribute data to the silo organization
(``ORGANIZATION_ID``) and key per-actor sessions by Discord user id —
same border as Slack/Telegram org + actor. CLI stays unbound and must not
import this module; Telegram uses ``telegram.principal``.
"""

from __future__ import annotations

import logging
import os

from config.constants.discord import DISCORD_SILO_GUILD_IDS_ENV
from config.principal import Actor, Principal, StorageScope
from gateway.core.billing.credits_client import organization_id_for_silo
from gateway.transports.discord.events import DM_GUILD_ID

logger = logging.getLogger(__name__)


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Discord turn's data cannot be established."""


def _silo_guild_allowlist() -> frozenset[str]:
    """Guild ids permitted to use the silo-org fallback, from env (may be empty)."""
    raw = os.getenv(DISCORD_SILO_GUILD_IDS_ENV) or ""
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


def resolve_discord_principal(*, guild_id: str) -> Principal:
    """Principal for a Discord turn: the organization this deployment serves.

    DMs use the sentinel guild id ``dm`` and skip the guild allowlist — they
    still require ``ORGANIZATION_ID`` and the messaging allowlist.
    """
    silo_org = organization_id_for_silo()
    if not silo_org:
        raise PrincipalResolutionError(
            "Discord turn cannot be resolved: no organization is configured for this deployment"
        )

    guild = (guild_id or "").strip() or DM_GUILD_ID
    if guild != DM_GUILD_ID:
        allowlist = _silo_guild_allowlist()
        if not allowlist:
            logger.warning(
                "[principal] serving Discord guild %s from the configured organization "
                "without an allowlist. Set %s to restrict this on a deployment holding "
                "real credentials.",
                guild,
                DISCORD_SILO_GUILD_IDS_ENV,
            )
        elif guild not in allowlist:
            raise PrincipalResolutionError(
                f"Discord guild {guild} is not an allowed guild for this deployment; "
                "refusing to attribute it to the configured organization"
            )

    return Principal.org(silo_org)


def discord_scope(principal: Principal, user_id: str) -> StorageScope:
    """Pair an organization with the Discord user acting in it."""
    return StorageScope(principal=principal, actor=Actor(id=user_id))


def resolve_discord_scope(*, guild_id: str, user_id: str) -> StorageScope:
    """Owning principal and acting member for one Discord turn."""
    return discord_scope(resolve_discord_principal(guild_id=guild_id), user_id)


__all__ = [
    "PrincipalResolutionError",
    "discord_scope",
    "resolve_discord_principal",
    "resolve_discord_scope",
]
