"""Grounding strings the assistant assembler turns into an envelope.

Callers (the turn builder, tests) fill this once; block contributors read from
it. Keeps assembly from growing another kwargs bag on every new CONTEXT fact.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssistantPromptParts:
    """One assistant turn's grounding, ready for tiered assembly."""

    reference: str
    history: str
    agents_md: str = ""
    docs: str = ""
    investigation_flow: str = ""
    prior_investigation: str = ""
    prior_action_facts: str = ""
    environment: str = ""
    long_term_memory: str = ""
    setup_state: str = ""
    surface: str = "interactive_shell"


__all__ = ["AssistantPromptParts"]
