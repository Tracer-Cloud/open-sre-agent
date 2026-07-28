"""The binding record and its key, shared by every backend."""

from __future__ import annotations

from dataclasses import dataclass

from config.principal import Actor, Principal

# The id a turn gets when it names no organization or no member: Telegram, the
# local CLI, and every row written before scoping existed. Empty string rather
# than None so it is a real key a lookup can match, and so a pre-scoping row
# adopted from the old index still resolves for the surface that wrote it.
#
# It is deliberately not a valid organization or Slack user id, so an unscoped
# lookup can never collide with a real one.
LEGACY_PRINCIPAL_ID = ""
LEGACY_ACTOR_ID = ""


def principal_id(principal: Principal | None) -> str:
    return LEGACY_PRINCIPAL_ID if principal is None else principal.id


def actor_id(actor: Actor | str | None) -> str:
    if actor is None:
        return LEGACY_ACTOR_ID
    if isinstance(actor, str):
        return actor.strip()
    return actor.id


@dataclass(frozen=True)
class BindingKey:
    """Identifies one conversation for one member of one principal."""

    platform: str
    chat_id: str
    principal_id: str
    actor_id: str

    @classmethod
    def build(
        cls,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> BindingKey:
        return cls(
            platform=platform,
            chat_id=chat_id,
            principal_id=principal_id(principal),
            actor_id=actor_id(actor),
        )


__all__ = [
    "LEGACY_ACTOR_ID",
    "LEGACY_PRINCIPAL_ID",
    "BindingKey",
    "actor_id",
    "principal_id",
]
