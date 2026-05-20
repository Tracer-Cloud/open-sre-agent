"""High-level non-command message routing package."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.evaluator import (
    handle_message_with_agent,
    llm_phase_route,
)

__all__ = [
    "handle_message_with_agent",
    "llm_phase_route",
]
