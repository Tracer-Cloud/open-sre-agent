"""Compound-prompt clause splitter."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PromptClause,
)

from .patterns import CLAUSE_SPLIT_RE


def split_prompt_clauses(message: str) -> list[PromptClause]:
    """Split compound prompts while preserving each clause's source position."""
    clauses: list[PromptClause] = []
    start = 0
    for match in CLAUSE_SPLIT_RE.finditer(message):
        raw = message[start : match.start()]
        stripped = raw.strip()
        if stripped:
            clauses.append(PromptClause(text=stripped, position=start + raw.index(stripped)))
        start = match.end()

    raw = message[start:]
    stripped = raw.strip()
    if stripped:
        clauses.append(PromptClause(text=stripped, position=start + raw.index(stripped)))

    return clauses or [PromptClause(text=message.strip(), position=0)]
