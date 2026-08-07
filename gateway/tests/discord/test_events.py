from __future__ import annotations

from gateway.transports.discord.events import DM_GUILD_ID, DiscordInboundMessage


def _inbound(guild_id: str) -> DiscordInboundMessage:
    return DiscordInboundMessage(
        guild_id=guild_id,
        user_id="U1",
        channel_id="C1",
        message_id="M1",
        thread_id="T1",
        text="hello",
    )


def test_conversation_key_includes_guild_channel_thread() -> None:
    assert _inbound("G1").conversation_key == "G1:C1:T1"


def test_is_guild_message_true_for_guild() -> None:
    assert _inbound("G1").is_guild_message is True


def test_is_guild_message_false_for_dm() -> None:
    assert _inbound(DM_GUILD_ID).is_guild_message is False
