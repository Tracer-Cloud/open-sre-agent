from __future__ import annotations

from unittest.mock import MagicMock

from gateway.transports.buzz.output_sink import BuzzOutputSink
from integrations.buzz.client import BuzzClient


def _client(*, send_ok: bool = True, edit_ok: bool = True) -> MagicMock:
    client = MagicMock(spec=BuzzClient)
    client.send_message.return_value = {
        "success": send_ok,
        "error": "" if send_ok else "boom",
        "event_id": "ev1" if send_ok else "",
    }
    client.edit_message.return_value = {"success": edit_ok, "error": "" if edit_ok else "boom"}
    return client


def test_init_posts_initial_status_message() -> None:
    client = _client()
    BuzzOutputSink(client=client, channel_id="chan-1")
    client.send_message.assert_called_once()
    assert client.send_message.call_args.kwargs["channel"] == "chan-1"


def test_finalize_edits_the_existing_message_in_place() -> None:
    client = _client()
    sink = BuzzOutputSink(client=client, channel_id="chan-1")

    sink.finalize("final answer")

    client.edit_message.assert_called_once_with(event_id="ev1", content="final answer")
    client.send_message.assert_called_once()  # only the initial status post


def test_finalize_falls_back_to_send_when_edit_fails() -> None:
    client = _client(edit_ok=False)
    sink = BuzzOutputSink(client=client, channel_id="chan-1")

    sink.finalize("final answer")

    assert client.send_message.call_count == 2
    assert client.send_message.call_args.kwargs["content"] == "final answer"


def test_stream_with_defer_returns_text_without_finalizing() -> None:
    client = _client()
    sink = BuzzOutputSink(client=client, channel_id="chan-1")

    text = sink.stream(label="answer", chunks=["hello ", "world"], defer_want_me_to_closer=True)

    assert text == "hello world"
    # Streaming throttled progress edits are expected; the deferred *final*
    # edit (the combined text) is what must not happen here.
    calls = [c.kwargs["content"] for c in client.edit_message.call_args_list]
    assert "hello world" not in calls
