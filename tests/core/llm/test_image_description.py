from __future__ import annotations

from typing import Any

from core.llm.image_description import describe_image_via_provider, is_supported_image


class _FakeAgent:
    """Stands in for the configured agent client's vision capability."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, Any]] = []

    def describe_image(
        self, image_bytes: bytes, mimetype: str, *, prompt: str, max_tokens: int, timeout: float
    ) -> str | None:
        self.calls.append(
            {
                "mimetype": mimetype,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "bytes": image_bytes,
            }
        )
        return self._text


def test_is_supported_image() -> None:
    assert is_supported_image("image/png") is True
    assert is_supported_image("image/jpeg; charset=binary") is True
    assert is_supported_image("text/plain") is False
    assert is_supported_image("application/pdf") is False


def test_describe_image_routes_to_configured_provider() -> None:
    # Arrange
    agent = _FakeAgent("A graph of error rates over time.")

    # Act
    out = describe_image_via_provider(b"\x89PNG\r\n", "image/png", agent=agent)

    # Assert: description returned; provider received the image + a bounded timeout.
    assert out == "A graph of error rates over time."
    assert agent.calls[0]["mimetype"] == "image/png"
    assert agent.calls[0]["timeout"] > 0


def test_describe_image_rejects_unsupported_mime_without_calling_provider() -> None:
    agent = _FakeAgent("unused")
    assert describe_image_via_provider(b"data", "application/pdf", agent=agent) is None
    assert agent.calls == []


def test_describe_image_none_on_empty_bytes() -> None:
    assert describe_image_via_provider(b"", "image/png", agent=_FakeAgent("x")) is None


def test_describe_image_none_when_provider_has_no_vision() -> None:
    class _NoVisionAgent:
        pass

    assert describe_image_via_provider(b"\x89PNG", "image/png", agent=_NoVisionAgent()) is None
