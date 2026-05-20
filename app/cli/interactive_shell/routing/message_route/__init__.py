"""High-level non-command message routing package.

Guardrail:
- Non-command routing intent comes from the LLM classifier path.
- Do not reintroduce deterministic regex/keyword fallback routing here.
"""

from __future__ import annotations

from app.cli.interactive_shell.routing.message_route.evaluator import (
    handle_message_with_agent,
    llm_phase_route,
)

__all__ = [
    "handle_message_with_agent",
    "llm_phase_route",
]
