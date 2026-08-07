"""Discord output sink — redaction and finalize characterization."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.transports.discord.output_sink import DiscordOutputSink


@pytest.fixture
def _patch_discord_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub Discord HTTP helpers so sink construction does not leave the process."""
    state: dict[str, Any] = {"edits": [], "posts": []}

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
