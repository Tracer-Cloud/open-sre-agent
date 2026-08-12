"""Normalize Discord gateway events into inbound turn payloads."""

from __future__ import annotations

from dataclasses import dataclass

#: Sentinel guild id used for direct messages (no guild context).
DM_GUILD_ID = "dm"


@dataclass(frozen=True)
class DiscordInboundAttachment:
    """One file attached to an inbound Discord message."""

    filename: str
    url: str
    content_type: str
    size: int


@dataclass(frozen=True)
class DiscordInboundMessage:
    """Normalized inbound Discord DM, mention, or thread follow-up."""

    guild_id: str
    user_id: str
    channel_id: str
    message_id: str
    thread_id: str
    text: str
    addressed: bool = True
    attachments: tuple[DiscordInboundAttachment, ...] = ()
    thread_history: tuple[tuple[str, str], ...] = ()

    @property
    def conversation_key(self) -> str:
        """Session binding key: one conversation per Discord thread root."""
        return f"{self.guild_id}:{self.channel_id}:{self.thread_id}"

    @property
    def is_guild_message(self) -> bool:
        """Whether this message arrived through a guild (server), not a DM."""
        return self.guild_id != DM_GUILD_ID
