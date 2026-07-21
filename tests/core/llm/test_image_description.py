from __future__ import annotations

from typing import Any

from core.llm.image_description import describe_image, is_supported_image


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.kwargs = kwargs
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


def test_is_supported_image() -> None:
    assert is_supported_image("image/png") is True
    assert is_supported_image("image/jpeg; charset=binary") is True
    assert is_supported_image("text/plain") is False
    assert is_supported_image("application/pdf") is False


def test_describe_image_returns_text_and_sends_an_image_block() -> None:
    # Arrange
    client = _FakeClient("A graph of error rates over time.")

    # Act
    out = describe_image(b"\x89PNG\r\n", "image/png", client=client, model="claude-test")

    # Assert: the description is returned and an image content block was sent.
    assert out == "A graph of error rates over time."
    content = client.messages.kwargs["messages"][0]["content"]  # type: ignore[index]
    assert any(block.get("type") == "image" for block in content)


def test_describe_image_rejects_unsupported_mime_without_calling_model() -> None:
    client = _FakeClient("unused")
    assert describe_image(b"data", "application/pdf", client=client) is None
    assert client.messages.kwargs is None


def test_describe_image_none_on_empty_bytes() -> None:
    assert describe_image(b"", "image/png", client=_FakeClient("x")) is None
