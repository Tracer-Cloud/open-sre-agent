"""Deterministic read-only actions for the interactive terminal assistant."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.terminal_intent import mentioned_integration_services


@dataclass(frozen=True)
class PlannedAction:
    """A safe slash command inferred from a natural-language terminal request."""

    kind: Literal["slash"]
    content: str
    position: int


@dataclass(frozen=True)
class PromptClause:
    """A single clause from a compound natural-language prompt."""

    text: str
    position: int


_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:check|verify|show|get|run)\b.{0,80}?\b(?:health|status)\b"
            r"|"
            r"\bopensre\s+health\b",
            re.IGNORECASE,
        ),
        "/health",
    ),
    (
        re.compile(
            r"\b(?:show|list|get|which|what)\b.{0,80}?"
            r"\b(?:connected\s+)?(?:services|integrations)\b",
            re.IGNORECASE,
        ),
        "/list integrations",
    ),
    (
        re.compile(
            r"\b(?:show|tell\s+me|get|what(?:'s|\s+is)?|current)\b.{0,80}?"
            r"\b(?:cli\s+)?version\b"
            r"|"
            r"\bopensre\s+version\b",
            re.IGNORECASE,
        ),
        "/version",
    ),
)

_INTEGRATION_DETAIL_RE = re.compile(
    r"\b(tell\s+me|show|list|get|what)\b.{0,120}?"
    r"\b(integrations?|services?|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)

_INTEGRATION_CAPABILITY_RE = re.compile(
    r"\b(what\b.{0,60}\bcan\s+do|can\s+do|does|about)\b",
    re.IGNORECASE,
)

_INTEGRATION_CONFIG_DETAIL_RE = re.compile(
    r"\b(show|list|get|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)

_CLAUSE_SPLIT_RE = re.compile(r"\s+\b(?:and(?:\s+then)?|then)\b\s+", re.IGNORECASE)


def _slash_action(command: str, position: int) -> PlannedAction:
    return PlannedAction(kind="slash", content=command, position=position)


def _split_prompt_clauses(message: str) -> list[PromptClause]:
    """Split compound prompts while preserving each clause's source position."""
    clauses: list[PromptClause] = []
    start = 0
    for match in _CLAUSE_SPLIT_RE.finditer(message):
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


def _plan_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    planned: list[PlannedAction] = []
    mentioned_services = mentioned_integration_services(clause.text)

    for pattern, command in _ACTION_PATTERNS:
        match = pattern.search(clause.text)
        if match is None or command in seen_slash:
            continue
        if command == "/list integrations" and mentioned_services:
            continue
        planned.append(_slash_action(command, clause.position + match.start()))
        seen_slash.add(command)

    lower = clause.text.lower()
    for service in mentioned_services:
        match = re.search(rf"\b{re.escape(service.replace('_', ' '))}\b", lower)
        position = clause.position + (match.start() if match else 0)

        # Capability questions should get an answer, not only configured-status output.
        relative_position = position - clause.position
        window_start = max(0, relative_position - 80)
        window_end = min(len(clause.text), relative_position + 120)
        window = clause.text[window_start:window_end]
        detail_window = clause.text[
            max(0, relative_position - 30) : min(len(clause.text), relative_position + 70)
        ]

        command = f"/integrations show {service}"
        wants_config_detail = _INTEGRATION_CONFIG_DETAIL_RE.search(detail_window) is not None
        capability_only = _INTEGRATION_CAPABILITY_RE.search(window) is not None
        if (
            command not in seen_slash
            and _INTEGRATION_DETAIL_RE.search(window)
            and wants_config_detail
            and not capability_only
        ):
            planned.append(_slash_action(command, position))
            seen_slash.add(command)

    return planned


def _plan_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    planned: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False

    for clause in _split_prompt_clauses(message):
        clause_actions = _plan_clause_actions(
            clause,
            seen_slash=seen_slash,
        )
        if not clause_actions:
            has_unhandled_clause = True
        planned.extend(clause_actions)

    return sorted(planned, key=lambda action: action.position), has_unhandled_clause


def _plan_actions(message: str) -> list[PlannedAction]:
    actions, _has_unhandled_clause = _plan_actions_with_unhandled(message)
    return actions


def plan_cli_actions(message: str) -> list[str]:
    """Return safe read-only slash commands requested by a natural-language turn."""
    return [action.content for action in _plan_actions(message) if action.kind == "slash"]


def plan_terminal_tasks(message: str) -> list[str]:
    """Return a test-friendly view of all deterministic terminal tasks."""
    return [action.kind for action in _plan_actions(message)]


def execute_cli_actions(message: str, session: ReplSession, console: Console) -> bool:
    """Execute inferred read-only CLI actions.

    Returns True when the message was handled. Unknown or ambiguous requests fall
    through to the LLM-backed assistant.
    """
    actions, has_unhandled_clause = _plan_actions_with_unhandled(message)
    if not actions:
        return False

    console.print()
    console.print("[bold cyan]assistant:[/bold cyan]")
    console.print("[dim]Running requested actions:[/dim]")
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        console.print()
        session.record("slash", action.content)
        console.print(f"[bold]$ {escape(action.content)}[/bold]")
        if not dispatch_slash(action.content, session, console):
            return True

    console.print()
    return not has_unhandled_clause


__all__ = ["execute_cli_actions", "plan_cli_actions", "plan_terminal_tasks"]
