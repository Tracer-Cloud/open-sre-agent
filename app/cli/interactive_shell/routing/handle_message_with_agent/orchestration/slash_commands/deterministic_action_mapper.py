"""Deterministic mapper from natural language to terminal actions."""

from __future__ import annotations

import re
from typing import TypedDict

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
    ParseError,
    PlannerUnavailable,
    PolicyError,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    extract_quoted_investigation_request_text,
    slash_action,
    split_prompt_clauses,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    SYNTHETIC_UNKNOWN_PREFIX,
    list_rds_postgres_scenarios,
)
from app.cli.support.exception_reporting import report_exception

from .mapper_runner import run_clause_rules


class ClauseTrace(TypedDict):
    clause_text: str
    clause_position: int
    rules: list[str]
    matched_action_kinds: list[str]


def _clause_only_investigation_followup(clause: PromptClause) -> bool:
    lower = clause.text.lower()
    if "investigation" in lower:
        return True
    return re.match(r'^\s*send\s+it\s+(?:"|\')', clause.text, re.IGNORECASE) is not None


def map_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    return run_clause_rules(clause, seen_slash=seen_slash).actions


def _map_actions_core(message: str) -> tuple[list[PlannedAction], bool, list[ClauseTrace]]:
    mapped: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False
    unmatched_clauses: list[PromptClause] = []
    traces: list[ClauseTrace] = []

    try:
        clauses = split_prompt_clauses(message)
    except Exception as exc:
        report_exception(
            exc,
            context="interactive_shell.routing.mapper.split_prompt_clauses",
            extra={"degrade_reason_tag": ParseError.reason_tag, "text_length": len(message)},
        )
        raise ParseError("Failed to split prompt into clauses for action mapping.") from exc

    try:
        for clause in clauses:
            trace_preview = run_clause_rules(clause, seen_slash=set(seen_slash))
            clause_actions = map_clause_actions(
                clause,
                seen_slash=seen_slash,
            )
            traces.append(
                {
                    "clause_text": clause.text,
                    "clause_position": clause.position,
                    "rules": list(trace_preview.trace),
                    "matched_action_kinds": [action.kind for action in clause_actions],
                }
            )
            if not clause_actions:
                has_unhandled_clause = True
                unmatched_clauses.append(clause)
            mapped.extend(clause_actions)
    except Exception as exc:
        report_exception(
            exc,
            context="interactive_shell.routing.mapper.map_clause_actions",
            extra={"degrade_reason_tag": PolicyError.reason_tag, "text_length": len(message)},
        )
        raise PolicyError("Failed to apply routing policy to one or more prompt clauses.") from exc

    try:
        has_investigation = any(action.kind == "investigation" for action in mapped)
        if not has_investigation:
            text_level_investigation = extract_quoted_investigation_request_text(message)
            if text_level_investigation is not None:
                mapped.append(text_level_investigation)
                has_investigation = True

        if (
            has_unhandled_clause
            and has_investigation
            and all(_clause_only_investigation_followup(clause) for clause in unmatched_clauses)
        ):
            has_unhandled_clause = False

        return sorted(mapped, key=lambda action: action.position), has_unhandled_clause, traces
    except Exception as exc:
        report_exception(
            exc,
            context="interactive_shell.routing.mapper.finalize",
            extra={
                "degrade_reason_tag": PlannerUnavailable.reason_tag,
                "text_length": len(message),
            },
        )
        raise PlannerUnavailable("Routing planner became unavailable during finalization.") from exc


def map_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    mapped, has_unhandled_clause, _trace = _map_actions_core(message)
    return mapped, has_unhandled_clause


def map_actions_with_trace(message: str) -> tuple[list[PlannedAction], bool, list[ClauseTrace]]:
    """Return actions plus explicit per-clause policy trace details."""
    return _map_actions_core(message)


def map_actions(message: str) -> list[PlannedAction]:
    actions, _has_unhandled_clause = map_actions_with_unhandled(message)
    return actions


def map_cli_actions(message: str) -> list[str]:
    """Return safe read-only slash commands and CLI commands requested by a natural-language turn."""
    return [
        action.content for action in map_actions(message) if action.kind in ("slash", "cli_command")
    ]


def map_terminal_tasks(message: str) -> list[str]:
    """Return a test-friendly view of all deterministic terminal tasks."""
    return [action.kind for action in map_actions(message)]


__all__ = [
    "SYNTHETIC_UNKNOWN_PREFIX",
    "list_rds_postgres_scenarios",
    "map_actions",
    "map_actions_with_trace",
    "map_actions_with_unhandled",
    "map_clause_actions",
    "map_cli_actions",
    "map_terminal_tasks",
    "slash_action",
]
