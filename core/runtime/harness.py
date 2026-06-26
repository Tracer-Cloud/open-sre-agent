"""Session-aware harness around the stateful Agent."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from core.runtime.agent import Agent
from core.runtime.events import AgentEventCallback, AgentEventKind
from core.runtime.types import (
    AgentHarnessContext,
    AgentLoopResult,
    AgentMessage,
    AgentSessionStore,
    IntegrationProvider,
    LlmFactory,
    SystemPromptProvider,
    ToolProvider,
)


class AgentHarness:
    """Bind model, tools, resources, persistence, and hooks for one surface."""

    def __init__(
        self,
        *,
        llm_factory: LlmFactory,
        system_prompt: SystemPromptProvider,
        tool_provider: ToolProvider,
        integration_provider: IntegrationProvider,
        max_iterations: int,
        store: AgentSessionStore | None = None,
        initial_messages: list[AgentMessage] | None = None,
    ) -> None:
        self.llm_factory = llm_factory
        self.system_prompt = system_prompt
        self.tool_provider = tool_provider
        self.integration_provider = integration_provider
        self.max_iterations = max_iterations
        self.store = store
        self.messages = (
            store.load_messages() if store is not None else list(initial_messages or [])
        )
        self._listeners: list[AgentEventCallback] = []

    def subscribe(self, listener: AgentEventCallback) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return _unsubscribe

    def prompt(self, text: str) -> AgentLoopResult:
        resolved = self.integration_provider()
        context = AgentHarnessContext(
            messages=list(self.messages),
            resolved_integrations=resolved,
        )
        prompt = (
            self.system_prompt(context)
            if callable(self.system_prompt)
            else self.system_prompt
        )
        agent = Agent(
            llm=self.llm_factory(),
            system_prompt=prompt,
            tools=self.tool_provider(context),
            resolved_integrations=resolved,
            messages=self.messages,
            max_iterations=self.max_iterations,
        )
        agent.subscribe(self._emit)
        result = agent.prompt(text)
        self.messages = result.messages
        if self.store is not None:
            self.store.save_messages(self.messages)
        return result

    def _emit(self, kind: AgentEventKind, data: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            listener(kind, data)


__all__ = ["AgentHarness"]
