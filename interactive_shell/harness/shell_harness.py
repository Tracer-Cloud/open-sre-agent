"""Interactive-shell adapters for the shared agent harness."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.runtime import AgentEventCallback, AgentHarness
from core.runtime.types import RuntimeTool
from tools.registered_tool import RegisteredTool


def create_shell_tool_gathering_harness(
    *,
    llm_factory: Callable[[], Any],
    system_prompt: str,
    tools: list[RegisteredTool],
    resolved_integrations: dict[str, Any],
    max_iterations: int,
    on_event: AgentEventCallback | None = None,
) -> AgentHarness:
    """Build the shell's live-data gathering harness.

    The shell still owns prompt rendering and final prose composition; this adapter
    moves the model/tool loop itself onto the shared ``AgentHarness`` stack.
    """

    harness = AgentHarness(
        llm_factory=llm_factory,
        system_prompt=system_prompt,
        tool_provider=lambda _context: tools,
        integration_provider=lambda: resolved_integrations,
        max_iterations=max_iterations,
    )
    if on_event is not None:
        harness.subscribe(on_event)
    return harness


def create_shell_action_harness(
    *,
    llm_factory: Callable[[], Any],
    system_prompt: str,
    tools: Sequence[RuntimeTool],
    max_iterations: int,
    on_event: AgentEventCallback | None = None,
) -> AgentHarness:
    """Build the shell action harness over first-class runtime tools."""

    harness = AgentHarness(
        llm_factory=llm_factory,
        system_prompt=system_prompt,
        tool_provider=lambda _context: list(tools),
        integration_provider=lambda: {},
        max_iterations=max_iterations,
    )
    if on_event is not None:
        harness.subscribe(on_event)
    return harness


__all__ = ["create_shell_action_harness", "create_shell_tool_gathering_harness"]
