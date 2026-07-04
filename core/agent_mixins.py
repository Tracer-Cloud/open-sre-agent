"""Reusable agent behavior mixins: event dispatch, tool filtering, steering.

``AgentEventEmitter`` forwards ``(kind, data)`` tuple events and typed runtime
events to optional callbacks. ``AgentToolFilter`` exposes the tool-narrowing
hook. ``AgentSteering`` is the stop/continue/redirect control-plane: queued
user messages injected into (steer) or appended after (follow_up) a run.
``Agent`` and any custom tool-calling loop compose these mixins.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from core.events import (
    RuntimeEvent,
    RuntimeEventCallback,
    TupleEventCallback,
    runtime_event_from_tuple,
    tuple_payload_from_event,
)
from core.messages import UserRuntimeMessage
from core.types import RuntimeTool

if TYPE_CHECKING:
    from core.messages import RuntimeMessage

logger = logging.getLogger(__name__)


class AgentEventEmitter:
    """Dispatch ``(kind, data)`` tuple events and typed runtime events to callbacks.

    Both callbacks default to ``None`` (no listener). Set ``_on_tuple_event`` /
    ``_on_runtime_event`` on the instance (in ``__init__`` or ``run``) to listen.
    Callback failures are swallowed — event rendering must never break the loop.
    """

    _on_tuple_event: TupleEventCallback | None = None
    _on_runtime_event: RuntimeEventCallback | None = None

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        event = runtime_event_from_tuple(kind, data)
        if event is not None:
            self._emit_runtime(event)
            return
        self._emit_tuple(kind, data)

    def _emit_runtime(self, event: RuntimeEvent) -> None:
        if self._on_runtime_event is not None:
            try:
                self._on_runtime_event(event)
            except Exception:  # noqa: BLE001 - event rendering must never break the loop
                logger.debug(
                    "[runtime] on_runtime_event(%s) raised; ignoring",
                    event.type,
                    exc_info=True,
                )
        payload = tuple_payload_from_event(event)
        if payload is not None:
            self._emit_tuple(*payload)

    def _emit_tuple(self, kind: str, data: dict[str, Any]) -> None:
        if self._on_tuple_event is not None:
            try:
                self._on_tuple_event(kind, data)
            except Exception:  # noqa: BLE001 - event rendering must never break the loop
                logger.debug("[runtime] on_event(%s) raised; ignoring", kind, exc_info=True)


class AgentToolFilter[RuntimeToolT: RuntimeTool]:
    """Hook to narrow the tool list the agent will see (identity by default)."""

    def _filter_tools(self, tools: list[RuntimeToolT]) -> list[RuntimeToolT]:
        return tools


class AgentSteering:
    """Stop/continue/redirect control-plane for the agent loop.

    ``steer`` queues a message injected before the *next* LLM turn; ``follow_up``
    queues one appended only once the loop would otherwise stop. Instances must
    initialize ``_steering_messages`` / ``_follow_up_messages`` (e.g. in
    ``Agent.__init__``) before use.
    """

    _steering_messages: deque[str]
    _follow_up_messages: deque[str]

    def steer(self, message: str) -> None:
        """Inject a user message into the active run before the next LLM turn."""
        if message.strip():
            self._steering_messages.append(message)

    def follow_up(self, message: str) -> None:
        """Queue a user message to run after the current turn would otherwise stop."""
        if message.strip():
            self._follow_up_messages.append(message)

    def _drain_steering_messages(self, messages: list[RuntimeMessage]) -> None:
        while self._steering_messages:
            messages.append(UserRuntimeMessage(content=self._steering_messages.popleft()))

    def _pop_follow_up_message(self) -> str | None:
        if not self._follow_up_messages:
            return None
        return self._follow_up_messages.popleft()


__all__ = ["AgentEventEmitter", "AgentSteering", "AgentToolFilter"]
