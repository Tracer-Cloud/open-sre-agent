"""Stable getting-started choices for the interactive agent."""

from __future__ import annotations

GETTING_STARTED_OPTIONS: tuple[str, ...] = (
    "Explore a repo and analyze its CI/CD performance (recommended)",
    "Set up an agent that improves CI/CD reliability over time",
    "Connect OpenSRE to Slack and hand off DevOps chores for your team",
)

_GETTING_STARTED_RULE = (
    "When the user asks what you can do, what you're capable of, how you can "
    "help, what tools you have, or for a demo / getting-started suggestion: "
    "call `ask_user_choice` with ONLY the getting-started options below, in "
    "the order shown. Use each option verbatim; do not rephrase, add, remove, "
    "or reorder options. The interactive surface adds `Or type your own "
    "answer...` automatically, so do not include a custom-answer option. Do "
    "not list platform features, slash commands, AGENTS.md capabilities, or "
    "add a Want-me-to closer that invents another action."
)


def load_getting_started_block() -> str:
    """Return the agent rule and exact selectable starter prompts."""
    lines = [_GETTING_STARTED_RULE, ""]
    lines.extend(f"- {option}" for option in GETTING_STARTED_OPTIONS)
    return "\n".join(lines)


__all__ = ["GETTING_STARTED_OPTIONS", "load_getting_started_block"]
