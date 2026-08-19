"""Per-turn output the host writes and a chat transport must accept.

This is the gateway's :class:`~core.agent_harness.OutputSink`: the same
ephemeral turn-delivery port the harness already names, plus live tool-status
and the final chat answer. It is not a store, a notifier, or a callback.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.agent_harness import OutputSink


@runtime_checkable
class GatewayOutputSink(OutputSink, Protocol):
    def set_tool_status(self, text: str) -> None:
        """Show live tool progress for the running turn."""

    def finalize(self, text: str) -> None:
        """Deliver the turn's final answer to the chat."""
