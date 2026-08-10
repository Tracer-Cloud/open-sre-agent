"""Decode ``assistant_handoff`` tool inputs into the typed handoff model.

Structured schema fields are first-class on :class:`AssistantHandoff`. Legacy
tag strings from :meth:`AssistantHandoff.to_handoff_contents` remain for
callers that still iterate ``handoff_contents`` tuples.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.turns.assistant_handoff import AssistantHandoff


def handoff_contents_from_tool_input(handoff_input: Mapping[str, Any]) -> tuple[str, ...]:
    """Return legacy handoff tag strings from one ``assistant_handoff`` tool input."""
    return AssistantHandoff.from_tool_input(handoff_input).to_handoff_contents()


__all__ = ["handoff_contents_from_tool_input"]
