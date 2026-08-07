from __future__ import annotations

from gateway.transports.discord.client import split_discord_content


def test_split_discord_content_under_limit() -> None:
    text = "hello world"
    assert split_discord_content(text) == [text]


def test_split_discord_content_splits_long_body() -> None:
    text = "a" * 2500
    parts = split_discord_content(text, limit=2000)
    assert len(parts) >= 2
    assert "".join(parts) == text
