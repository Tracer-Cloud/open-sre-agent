"""Plan deterministic actions from natural-language REPL input."""

from __future__ import annotations

import re

from app.cli.interactive_shell.intent_parser import (
    ACTION_PATTERNS,
    INTEGRATION_CAPABILITY_RE,
    INTEGRATION_CONFIG_DETAIL_RE,
    INTEGRATION_DETAIL_RE,
    SAMPLE_ALERT_RE,
    SYNTHETIC_RDS_TEST_RE,
    extract_llm_provider_switch,
    extract_shell_command,
    sample_alert_action,
    slash_action,
    split_prompt_clauses,
    synthetic_test_action,
)
from app.cli.interactive_shell.interaction_models import PlannedAction, PromptClause
from app.cli.interactive_shell.terminal_intent import mentioned_integration_services


def plan_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    planned: list[PlannedAction] = []
    mentioned_services = mentioned_integration_services(clause.text)

    for pattern, command in ACTION_PATTERNS:
        match = pattern.search(clause.text)
        if match is None or command in seen_slash:
            continue
        if command == "/list integrations" and mentioned_services:
            continue
        planned.append(slash_action(command, clause.position + match.start()))
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

        slash = f"/integrations show {service}"
        wants_config_detail = INTEGRATION_CONFIG_DETAIL_RE.search(detail_window) is not None
        capability_only = INTEGRATION_CAPABILITY_RE.search(window) is not None
        if (
            slash not in seen_slash
            and INTEGRATION_DETAIL_RE.search(window)
            and wants_config_detail
            and not capability_only
        ):
            planned.append(slash_action(slash, position))
            seen_slash.add(slash)

    if planned:
        return planned

    provider_switch_action = extract_llm_provider_switch(clause)
    if provider_switch_action is not None:
        planned.append(provider_switch_action)
        return planned

    synthetic_match = SYNTHETIC_RDS_TEST_RE.search(clause.text)
    if synthetic_match is not None:
        planned.append(
            synthetic_test_action("rds_postgres", clause.position + synthetic_match.start())
        )
        return planned

    sample_match = SAMPLE_ALERT_RE.search(clause.text)
    if sample_match is not None:
        planned.append(sample_alert_action("generic", clause.position + sample_match.start()))
        return planned

    planned_shell = extract_shell_command(clause)
    if planned_shell is not None:
        planned.append(planned_shell)

    return planned


def plan_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    planned: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False

    for clause in split_prompt_clauses(message):
        clause_actions = plan_clause_actions(
            clause,
            seen_slash=seen_slash,
        )
        if not clause_actions:
            has_unhandled_clause = True
        planned.extend(clause_actions)

    return sorted(planned, key=lambda action: action.position), has_unhandled_clause


def plan_actions(message: str) -> list[PlannedAction]:
    actions, _has_unhandled_clause = plan_actions_with_unhandled(message)
    return actions


def plan_cli_actions(message: str) -> list[str]:
    """Return safe read-only slash commands requested by a natural-language turn."""
    return [action.content for action in plan_actions(message) if action.kind == "slash"]


def plan_terminal_tasks(message: str) -> list[str]:
    """Return a test-friendly view of all deterministic terminal tasks."""
    return [action.kind for action in plan_actions(message)]


__all__ = [
    "plan_actions",
    "plan_actions_with_unhandled",
    "plan_cli_actions",
    "plan_clause_actions",
    "plan_terminal_tasks",
]
