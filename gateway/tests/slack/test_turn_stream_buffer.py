"""TDD: Slack ``_TurnStream`` answer buffering must not use string ``+=``.

Many tiny markdown chunks arrive on the stream path. Buffering with
``s += chunk`` is quadratic; a part list + ``join`` on flush is linear.
These tests pin the observable delivery and the buffer shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from gateway.transports.slack.output_sink import _TurnStream


def _stream(*, update_interval_seconds: float = 10_000.0) -> tuple[_TurnStream, MagicMock]:
    client = MagicMock()
    client.start_stream.return_value = "ts.1"
    client.append_stream.return_value = True
    client.stop_stream.return_value = True
    stream = _TurnStream(
        client=client,
        channel_id="C1",
        thread_ts="t.1",
        update_interval_seconds=update_interval_seconds,
        on_started=lambda: None,
    )
    return stream, client


def test_many_chunks_are_held_as_parts_until_flush() -> None:
    # Arrange — long update interval so appends do not flush mid-burst.
    stream, _client = _stream(update_interval_seconds=10_000.0)

    # Act
    for i in range(200):
        assert stream.append_text(f"c{i}") is True

    # Assert: buffer is a list of chunks (not one growing string).
    assert isinstance(stream._pending_parts, list)
    assert len(stream._pending_parts) == 200
    assert stream._joined_pending() == "".join(f"c{i}" for i in range(200))


def test_finish_joins_pending_parts_into_one_stream_append() -> None:
    # Arrange
    stream, client = _stream(update_interval_seconds=10_000.0)
    chunks = ["Hello", ", ", "world", "!"]
    for chunk in chunks:
        stream.append_text(chunk)

    # Act
    ok = stream.finish("".join(chunks), blocks=None)

    # Assert
    assert ok is True
    markdown = [
        c
        for call in client.append_stream.call_args_list
        for c in (call.kwargs.get("chunks") or [])
        if c.get("type") == "markdown_text"
    ]
    assert markdown, "expected a markdown_text flush on finish"
    assert markdown[-1]["text"] == "Hello, world!"
    assert stream._pending_parts == []


def test_mid_burst_flush_clears_parts_and_keeps_order() -> None:
    # Arrange — zero interval forces a flush after every append once started.
    stream, client = _stream(update_interval_seconds=0.0)
    stream.append_text("a")
    stream.append_text("b")
    stream.append_text("c")

    # Act
    stream.finish("abc", blocks=None)

    # Assert: full answer delivered in order across flushes.
    texts = [
        c["text"]
        for call in client.append_stream.call_args_list
        for c in (call.kwargs.get("chunks") or [])
        if c.get("type") == "markdown_text"
    ]
    assert "".join(texts) == "abc"


def test_finish_without_start_returns_false() -> None:
    stream, client = _stream()
    assert stream.finish("never streamed", blocks=None) is False
    client.stop_stream.assert_not_called()


def test_finish_with_unrelated_text_appends_after_streamed_prefix() -> None:
    # Arrange — stream a partial answer, then finalize with error copy.
    stream, client = _stream(update_interval_seconds=10_000.0)
    stream.append_text("partial")

    # Act
    ok = stream.finish("timeout notice", blocks=None)

    # Assert
    assert ok is True
    texts = [
        c["text"]
        for call in client.append_stream.call_args_list
        for c in (call.kwargs.get("chunks") or [])
        if c.get("type") == "markdown_text"
    ]
    assert "".join(texts) == "partial\n\ntimeout notice"
    assert stream._pending_parts == []
