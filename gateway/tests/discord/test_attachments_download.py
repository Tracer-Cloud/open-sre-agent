"""Attachment fetching — the bot token must never leave the Discord CDN.

Two defences are pinned here: the host allowlist rejects off-Discord URLs
before any request, and redirects are not followed so a 302 from a compromised
CDN path cannot replay the ``Authorization`` header at an attacker's host.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from gateway.transports.discord import attachments
from gateway.transports.discord.attachments import is_allowed_attachment_url

# A value that would be unmistakable if it ever appeared in an outbound request.
_TOKEN = "bot-token-must-never-leave-discord"
_DISCORD_URL = "https://cdn.discordapp.com/attachments/1/2/file.txt"


class _FakeResponse:
    """Stands in for the streamed httpx response context manager."""

    def __init__(self, status_code: int, chunks: tuple[bytes, ...] = ()) -> None:
        self.status_code = status_code
        self._chunks = chunks

    def iter_bytes(self) -> Iterator[bytes]:
        yield from self._chunks

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _RecordingStream:
    """Captures every request `_download` attempts."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response


def test_discord_cdn_https_allowed() -> None:
    assert is_allowed_attachment_url(_DISCORD_URL)
    assert is_allowed_attachment_url("https://media.discordapp.net/attachments/1/2/img.png")


def test_non_discord_host_rejected() -> None:
    assert not is_allowed_attachment_url("https://evil.example/steal")
    assert not is_allowed_attachment_url("http://cdn.discordapp.com/attachments/1/2/x")
    assert not is_allowed_attachment_url("https://discordapp.com.evil.example/x")


def test_off_discord_url_is_never_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected host must not produce a request at all, token or otherwise."""
    # Arrange
    stream = _RecordingStream(_FakeResponse(httpx.codes.OK, (b"payload",)))
    monkeypatch.setattr(attachments.httpx, "stream", stream)

    # Act
    result = attachments._download("https://evil.example/steal", _TOKEN)

    # Assert
    assert result is None
    assert stream.calls == []


def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 off the CDN yields nothing; httpx must not chase it with the token."""
    # Arrange
    stream = _RecordingStream(_FakeResponse(httpx.codes.FOUND))
    monkeypatch.setattr(attachments.httpx, "stream", stream)

    # Act
    result = attachments._download(_DISCORD_URL, _TOKEN)

    # Assert: no body, exactly one request, and redirects disabled on it.
    assert result is None
    assert len(stream.calls) == 1
    assert stream.calls[0]["follow_redirects"] is False
    assert stream.calls[0]["url"] == _DISCORD_URL


def test_allowed_url_downloads_and_sends_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive path, so the negatives above cannot pass by doing nothing."""
    # Arrange
    stream = _RecordingStream(_FakeResponse(httpx.codes.OK, (b"hello ", b"world")))
    monkeypatch.setattr(attachments.httpx, "stream", stream)

    # Act
    result = attachments._download(_DISCORD_URL, _TOKEN)

    # Assert
    assert result == b"hello world"
    assert stream.calls[0]["headers"]["Authorization"] == f"Bot {_TOKEN}"
