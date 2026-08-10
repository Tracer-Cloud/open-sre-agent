"""Every chat surface is an external surface, and must say so.

``core`` decides whether to show a raw tool payload or a receipt by reading
``redacts_raw_tool_output`` off the sink it was handed. The default is *not*
redacting — the local terminal is not an external surface and carries no such
attribute — so a transport that forgets the flag leaks tool output into a shared
channel silently, with nothing failing. That is the incident this pins: 14 MB of
pod logs, including customer email addresses, posted into a Slack thread.
"""

from __future__ import annotations

from typing import Any

from gateway.core.runtime.live_sink import LiveOutputSink
from gateway.transports.buzz.output_sink import BuzzOutputSink
from gateway.transports.discord.output_sink import DiscordOutputSink
from gateway.transports.slack.output_sink import SlackOutputSink
from gateway.transports.telegram.output_sink import GatewayOutputSink as TelegramOutputSink

#: Every sink that delivers a turn to a chat surface, plus the per-turn holder
#: that stands in for one on the agent. Add a transport, add it here.
_EXTERNAL_SINKS: tuple[type[Any], ...] = (
    LiveOutputSink,
    SlackOutputSink,
    DiscordOutputSink,
    TelegramOutputSink,
    BuzzOutputSink,
)


def test_every_chat_sink_declares_itself_an_external_surface() -> None:
    missing = [sink.__name__ for sink in _EXTERNAL_SINKS if not sink.redacts_raw_tool_output]

    assert not missing, f"chat sinks that would receive raw tool payloads: {missing}"
