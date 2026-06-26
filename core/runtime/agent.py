"""Stateful agent wrapper around the pure agent loop."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import core.runtime.agent_loop as agent_loop_module
from core.runtime.events import AgentEventCallback, AgentEventKind
from core.runtime.types import AgentLoopResult, AgentMessage
from tools.registered_tool import RegisteredTool


class PendingMessageQueue:
    """Small one-at-a-time/all-at-once message queue used by Agent."""

    def __init__(self, mode: str = "one-at-a-time") -> None:
        self.mode = mode
        self._messages: list[AgentMessage] = []

    def enqueue(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def clear(self) -> None:
        self._messages.clear()

    def has_items(self) -> bool:
        return bool(self._messages)

    def drain(self) -> list[AgentMessage]:
        if not self._messages:
            return []
        if self.mode == "all":
            drained = list(self._messages)
            self._messages.clear()
            return drained
        return [self._messages.pop(0)]


class Agent:
    """Own in-memory transcript state and run lifecycle for one agent."""

    def __init__(
        self,
        *,
        llm: object,
        system_prompt: str,
        tools: list[RegisteredTool],
        resolved_integrations: dict[str, Any],
        messages: list[AgentMessage] | None = None,
        max_iterations: int,
    ) -> None:
        self.llm = llm
        self.system_prompt = system_prompt
        self.tools = list(tools)
        self.resolved_integrations = dict(resolved_integrations)
        self.messages = list(messages or [])
        self.max_iterations = max_iterations
        self.is_running = False
        self.pending_tool_calls: set[str] = set()
        self.error_message: str | None = None
        self._listeners: list[AgentEventCallback] = []
        self._steering_queue = PendingMessageQueue()
        self._follow_up_queue = PendingMessageQueue()

    def subscribe(self, listener: AgentEventCallback) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return _unsubscribe

    def steer(self, message: AgentMessage) -> None:
        self._steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        self._follow_up_queue.enqueue(message)

    def abort(self) -> None:
        self.error_message = "Agent abort requested; blocking runtime will stop at the next boundary."

    def wait_for_idle(self) -> None:
        return None

    def prompt(self, text: str) -> AgentLoopResult:
        self._raise_if_running()
        self.messages.append({"role": "user", "content": text})
        return self._run()

    def continue_(self) -> AgentLoopResult:
        if not self.messages:
            msg = "Cannot continue: no messages in context"
            raise ValueError(msg)
        self._raise_if_running()
        return self._run()

    def _run(self) -> AgentLoopResult:
        self._raise_if_running()
        self.is_running = True
        self.error_message = None
        try:
            result = agent_loop_module.run_agent_loop(
                llm=self.llm,
                system=self.system_prompt,
                messages=self.messages,
                tools=self.tools,
                resolved_integrations=self.resolved_integrations,
                max_iterations=self.max_iterations,
                on_event=self._process_event,
            )
            self.messages = result.messages
            return result
        except Exception as exc:
            self.error_message = str(exc)
            raise
        finally:
            self.pending_tool_calls.clear()
            self.is_running = False

    def _process_event(self, kind: AgentEventKind, data: dict[str, Any]) -> None:
        if kind == "tool_start":
            tool_id = str(data.get("id") or "")
            if tool_id:
                self.pending_tool_calls.add(tool_id)
        elif kind == "tool_end":
            tool_id = str(data.get("id") or "")
            if tool_id:
                self.pending_tool_calls.discard(tool_id)

        for listener in list(self._listeners):
            listener(kind, data)

    def _raise_if_running(self) -> None:
        if self.is_running:
            msg = "Agent is already running"
            raise RuntimeError(msg)


__all__ = ["Agent", "PendingMessageQueue"]
