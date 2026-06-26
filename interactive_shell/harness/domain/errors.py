"""Typed errors for the interactive-shell harness."""

from __future__ import annotations


class PlannerLLMError(Exception):
    """LLM call inside the action planner failed.

    Carries a user-friendly message (from the CLI adapter's explain_failure
    path) so the caller can display it inside the assistant block instead of
    emitting a raw log warning above the response.
    """


__all__ = [
    "PlannerLLMError",
]
