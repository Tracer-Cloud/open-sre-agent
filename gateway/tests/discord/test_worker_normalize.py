from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

from gateway.transports.discord.worker import normalize_message


def _message(
    *,
    content: str = "hi",
    channel: object,
    author_id: int = 1,
    mentions: list[object] | None = None,
) -> SimpleNamespace:
    author = SimpleNamespace(bot=False, id=author_id)
    return SimpleNamespace(
        author=author,
        content=content,
        channel=channel,
        guild=SimpleNamespace(id=99),
        attachments=[],
        mentions=mentions or [],
        id=555,
    )


def test_dm_is_addressed() -> None:
    channel = MagicMock(spec=discord.DMChannel)
    msg = _message(content="hello", channel=channel)
    inbound = normalize_message(msg, bot_user_id="42")  # type: ignore[arg-type]
    assert inbound is not None
    assert inbound.addressed is True


def test_guild_message_without_mention_is_ignored() -> None:
    channel = MagicMock(spec=discord.TextChannel)
    msg = _message(content="hello", channel=channel)
    inbound = normalize_message(msg, bot_user_id="42")  # type: ignore[arg-type]
    assert inbound is None


def test_mention_sets_addressed() -> None:
    channel = MagicMock(spec=discord.TextChannel)
    bot = SimpleNamespace(id=42)
    msg = _message(content="<@42> ping", channel=channel, mentions=[bot])
    inbound = normalize_message(msg, bot_user_id="42")  # type: ignore[arg-type]
    assert inbound is not None
    assert inbound.addressed is True


def test_mention_then_slash_routes_as_literal_slash() -> None:
    channel = MagicMock(spec=discord.TextChannel)
    bot = SimpleNamespace(id=42)
    msg = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=1),
        content="<@42> /investigate generic",
        channel=channel,
        guild=SimpleNamespace(id=99),
        attachments=[],
        mentions=[bot],
        id=555,
    )
    inbound = normalize_message(msg, bot_user_id="42")  # type: ignore[arg-type]
    assert inbound is not None
    assert inbound.text == "/investigate generic"
