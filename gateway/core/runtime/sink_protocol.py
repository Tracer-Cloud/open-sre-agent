"""Structural types for gateway output sinks and the per-message callback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from core.agent_harness.ports import OutputSink
from core.agent_harness.session import SessionCore


@runtime_checkable
class GatewaySink(OutputSink, Protocol):
    """An :class:`OutputSink` with the gateway's per-turn status and final-answer hooks."""

    def set_tool_status(self, text: str) -> None:
        """Show live tool progress for the running turn."""

    def finalize(self, text: str) -> None:
        """Deliver the turn's final answer to the chat."""


# The transport-agnostic per-message callback: ``(text, session, sink, logger)``.
GatewayAgentCallback = Callable[[str, SessionCore, GatewaySink, logging.Logger], None]

__all__ = ["GatewayAgentCallback", "GatewaySink"]
