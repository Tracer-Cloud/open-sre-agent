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

    #: External surfaces must not receive raw tool payloads (AGENTS.md CWE-209
    #: boundary rule). ``core`` reads this with ``getattr`` so the local terminal
    #: sink, which is not an external surface, needs no such attribute.
    redacts_raw_tool_output: bool

    def set_tool_status(self, text: str, *, call_id: str | None = None) -> None:
        """Show live tool progress for the running turn.

        ``call_id`` identifies the tool call so its timeline row can later be
        closed with the outcome it actually had.
        """

    def end_tool_status(self, *, failed: bool, call_id: str | None = None) -> None:
        """Close the timeline row opened for ``call_id`` with its real outcome."""

    def leave_tool_status_open(
        self, *, call_id: str | None = None, title: str | None = None
    ) -> None:
        """Abandon the timeline row for ``call_id`` without giving it an outcome.

        For a tool that handed work off to a background run: the row must not read
        complete, and no later sweep may close it either. ``title`` optionally
        replaces the row's text (e.g. to say the work was handed off), best-effort.
        """

    def finalize(self, text: str, *, failed: bool = False) -> None:
        """Deliver the turn's final answer to the chat.

        ``failed`` marks any still-open timeline row as errored — a turn that
        timed out, was stopped, or produced no answer did not succeed.
        """


# The transport-agnostic per-message callback: ``(text, session, sink, logger)``.
GatewayAgentCallback = Callable[[str, SessionCore, GatewaySink, logging.Logger], None]

__all__ = ["GatewayAgentCallback", "GatewaySink"]
