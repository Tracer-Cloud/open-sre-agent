"""Interactive-shell adapters for the shared runtime loop."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.runtime import LoopEventCallback, ToolLoopResult, run_tool_calling_loop
from core.runtime.types import RuntimeTool


class ShellActionHarness:
    """Small action-selection harness over the shared tool-calling loop."""

    def __init__(
        self,
        *,
        llm_factory: Callable[[], Any],
        system_prompt: str,
        tools: Sequence[RuntimeTool],
        max_iterations: int,
        on_event: LoopEventCallback | None = None,
    ) -> None:
        self._llm_factory = llm_factory
        self._system_prompt = system_prompt
        self._tools = list(tools)
        self._max_iterations = max_iterations
        self._on_event = on_event

    def prompt(self, text: str) -> ToolLoopResult:
        return run_tool_calling_loop(
            llm=self._llm_factory(),
            system=self._system_prompt,
            messages=[{"role": "user", "content": text}],
            tools=self._tools,
            resolved_integrations={},
            max_iterations=self._max_iterations,
            on_event=self._on_event,
        )


def create_shell_action_harness(
    *,
    llm_factory: Callable[[], Any],
    system_prompt: str,
    tools: Sequence[RuntimeTool],
    max_iterations: int,
    on_event: LoopEventCallback | None = None,
) -> ShellActionHarness:
    """Build the shell action harness over first-class runtime tools."""
    return ShellActionHarness(
        llm_factory=llm_factory,
        system_prompt=system_prompt,
        tools=tools,
        max_iterations=max_iterations,
        on_event=on_event,
    )


__all__ = ["ShellActionHarness", "create_shell_action_harness"]
