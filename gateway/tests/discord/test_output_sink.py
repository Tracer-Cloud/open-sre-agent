"""Discord output sink — redaction and finalize characterization."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.transports.discord.output_sink import DiscordOutputSink


@pytest.fixture
def _patch_discord_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub Discord HTTP helpers so sink construction does not leave the process."""
    # ``final`` records only component-bearing calls. A Discord answer is
    # "delivered" exactly when the feedback components go out; plain ``edits``
    # also contains throttled in-progress previews, so counting them cannot
    # tell delivery from a preview.
    state: dict[str, Any] = {"edits": [], "posts": [], "final": []}

    def _send_message(*, channel_id: str, content: str, bot_token: str) -> str:
        _ = (channel_id, bot_token)
        state["posts"].append(content)
        return "msg-1"

    def _edit_message(
        *,
        channel_id: str,
        message_id: str,
        content: str,
        bot_token: str,
    ) -> bool:
        _ = (channel_id, message_id, bot_token)
        state["edits"].append(content)
        return True

    def _edit_with_components(
        *,
        channel_id: str,
        message_id: str,
        content: str,
        components: Any,
        bot_token: str,
    ) -> bool:
        _ = (channel_id, message_id, components, bot_token)
        state["edits"].append(content)
        state["final"].append(content)
        return True

    def _send_with_components(
        *,
        channel_id: str,
        content: str,
        components: Any,
        bot_token: str,
    ) -> str:
        _ = (channel_id, components, bot_token)
        state["posts"].append(content)
        state["final"].append(content)
        return "msg-extra"

    monkeypatch.setattr(
        "gateway.transports.discord.output_sink.send_message",
        _send_message,
    )
    monkeypatch.setattr(
        "gateway.transports.discord.output_sink.edit_message",
        _edit_message,
    )
    monkeypatch.setattr(
        "gateway.transports.discord.output_sink.edit_message_with_components",
        _edit_with_components,
    )
    monkeypatch.setattr(
        "gateway.transports.discord.output_sink.send_message_with_components",
        _send_with_components,
    )
    monkeypatch.setattr(
        "gateway.transports.discord.output_sink.feedback_components",
        lambda: [],
    )
    return state


def test_render_error_hides_raw_detail_behind_generic_copy(
    _patch_discord_client: dict[str, Any],
) -> None:
    # Arrange
    sink = DiscordOutputSink(
        bot_token="tok",
        channel_id="chan-1",
        edit_interval_seconds=0.0,
    )

    # Act: hand render_error a raw exception string with sensitive detail.
    sink.render_error("RuntimeError: token sk-DO-NOT-LEAK rejected by db-host:5432")

    # Assert: the channel shows generic copy, none of the raw detail.
    finalized = _patch_discord_client["edits"][-1]
    assert finalized == "Something went wrong handling that request. Please try again."
    assert "sk-DO-NOT-LEAK" not in finalized
    assert "db-host" not in finalized


def test_render_error_keeps_credit_exhaustion_guidance(
    _patch_discord_client: dict[str, Any],
) -> None:
    from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER

    sink = DiscordOutputSink(
        bot_token="tok",
        channel_id="chan-1",
        edit_interval_seconds=0.0,
    )
    sink.render_error(f"Anthropic {CREDIT_EXHAUSTED_MARKER}. Original error: 400")
    finalized = _patch_discord_client["edits"][-1]
    assert "opensre auth login" in finalized
    assert "400" not in finalized


def test_sink_accepts_tool_hooks_attribute(
    _patch_discord_client: dict[str, Any],
) -> None:
    hooks = MagicMock(name="approval_hooks")
    sink = DiscordOutputSink(
        bot_token="tok",
        channel_id="chan-1",
        edit_interval_seconds=0.0,
        tool_hooks=hooks,
    )
    assert sink.tool_hooks is hooks


def test_stream_without_defer_delivers_the_answer(
    _patch_discord_client: dict[str, Any],
) -> None:
    """A non-deferred stream must deliver, not just leave a throttled preview.

    Discord used to discard ``defer_want_me_to_closer`` and never call
    ``finalize`` on any path, so an answered turn (the turn handler skips its
    own finalize when ``answered`` is true) reached the channel as an
    in-progress preview and never as a final message.
    """
    sink = DiscordOutputSink(
        bot_token="tok",
        channel_id="chan",
        edit_interval_seconds=0.0,
    )
    _patch_discord_client["final"].clear()

    result = sink.stream(
        label="assistant",
        chunks=iter(["test", " message"]),
        defer_want_me_to_closer=False,
    )

    assert result == "test message"
    assert _patch_discord_client["final"] == ["test message"]


def test_stream_with_defer_holds_delivery_until_finish_streamed_response(
    _patch_discord_client: dict[str, Any],
) -> None:
    """A deferred stream delivers nothing until the orchestrator flushes."""
    sink = DiscordOutputSink(
        bot_token="tok",
        channel_id="chan",
        edit_interval_seconds=0.0,
    )
    _patch_discord_client["final"].clear()

    result = sink.stream(
        label="assistant",
        chunks=iter(["deferred", " message"]),
        defer_want_me_to_closer=True,
    )

    assert result == "deferred message"
    assert _patch_discord_client["final"] == []

    sink.finish_streamed_response("deferred message")

    assert _patch_discord_client["final"] == ["deferred message"]


def test_a_second_goal_turn_posts_instead_of_overwriting(
    _patch_discord_client: dict[str, Any],
) -> None:
    """Outer-goal continuation runs several turns through one sink.

    The first answer replaces the placeholder by editing it. A later turn must
    deliver a new message — editing the same one overwrites an answer the user
    already read, and returning early would drop the later turn entirely.
    """
    # Arrange.
    sink = DiscordOutputSink(bot_token="t", channel_id="c", edit_interval_seconds=0.0)
    sink._message_id = "msg-1"

    # Act: two turns of one continued goal.
    sink.finalize("turn one answer")
    posts_after_first = len(_patch_discord_client["posts"])
    sink.finalize("turn two answer")

    # Assert: turn two was delivered, and not by overwriting turn one.
    assert len(_patch_discord_client["posts"]) > posts_after_first, (
        "second goal turn was dropped instead of posted"
    )
    assert "turn two answer" in _patch_discord_client["posts"][-1]
    assert "turn one answer" in _patch_discord_client["edits"][0]
