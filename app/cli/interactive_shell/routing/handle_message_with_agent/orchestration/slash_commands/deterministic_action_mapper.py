"""Deterministic mapper from natural language to terminal actions."""

from __future__ import annotations

import re

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    ACTION_PATTERNS,
    INTEGRATION_CAPABILITY_RE,
    INTEGRATION_CONFIG_DETAIL_RE,
    INTEGRATION_DETAIL_RE,
    SAMPLE_ALERT_RE,
    SYNTHETIC_RDS_TEST_RE,
    cli_command_action,
    extract_implementation_request,
    extract_llm_provider_switch,
    extract_quoted_investigation_request,
    extract_quoted_investigation_request_text,
    extract_shell_command,
    extract_task_cancel_request,
    mentioned_integration_services,
    normalize_intent_text,
    sample_alert_action,
    slash_action,
    split_prompt_clauses,
    synthetic_test_action,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    DEFAULT_SYNTHETIC_SCENARIO,
    SYNTHETIC_UNKNOWN_PREFIX,
    list_rds_postgres_scenarios,
)

_SYNTHETIC_SCENARIO_ID_RE = re.compile(
    r"\b(?P<scenario>\d{3}-[a-z0-9][a-z0-9-]*)\b",
    re.IGNORECASE,
)
_SYNTHETIC_NUMERIC_HINT_RE = re.compile(r"\b(?P<num>\d{1,4})\b")
_SYNTHETIC_ALL_RE = re.compile(
    r"\b(?:all|entire)\b.{0,40}\b(?:synthetic|benchmark|tests?)\b"
    r"|"
    r"\b(?:synthetic|benchmark|tests?)\b.{0,40}\b(?:all|entire)\b"
    r"|"
    r"\bfull\s+(?:synthetic(?:\s+tests?)?|benchmark|suite)\b"
    r"|"
    r"\b(?:synthetic|benchmark|tests?)\b.{0,40}\bfull\s+suite\b",
    re.IGNORECASE,
)


def _resolve_numeric_hint(text: str, scenarios: tuple[str, ...]) -> tuple[str, int] | None:
    for match in _SYNTHETIC_NUMERIC_HINT_RE.finditer(text):
        raw = match.group("num")
        padded = raw.zfill(3) if len(raw) <= 3 else raw
        matched = [name for name in scenarios if name.startswith(f"{padded}-")]
        if matched:
            return matched[0], match.start()
    return None


def _detect_unresolved_numeric_hint(text: str, scenarios: tuple[str, ...]) -> str | None:
    for match in _SYNTHETIC_NUMERIC_HINT_RE.finditer(text):
        raw = match.group("num")
        padded = raw.zfill(3) if len(raw) <= 3 else raw
        if not any(name.startswith(f"{padded}-") for name in scenarios):
            return raw
    return None


def _synthetic_action_content(clause: PromptClause, *, synthetic_start: int) -> tuple[str, int]:
    if _SYNTHETIC_ALL_RE.search(clause.text) is not None:
        return (
            "rds_postgres:all",
            clause.position + synthetic_start,
        )

    full_match = _SYNTHETIC_SCENARIO_ID_RE.search(clause.text)
    if full_match is not None:
        scenario_id = full_match.group("scenario").lower()
        return (
            f"rds_postgres:{scenario_id}",
            clause.position + full_match.start("scenario"),
        )

    scenarios = list_rds_postgres_scenarios()
    resolved = _resolve_numeric_hint(clause.text, scenarios)
    if resolved is not None:
        scenario_id, match_start = resolved
        return (
            f"rds_postgres:{scenario_id}",
            clause.position + match_start,
        )

    unresolved_hint = _detect_unresolved_numeric_hint(clause.text, scenarios)
    if unresolved_hint is not None:
        return (
            f"{SYNTHETIC_UNKNOWN_PREFIX}{unresolved_hint}",
            clause.position + synthetic_start,
        )

    return (
        f"rds_postgres:{DEFAULT_SYNTHETIC_SCENARIO}",
        clause.position + synthetic_start,
    )


def map_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    mapped: list[PlannedAction] = []

    normalized_text = normalize_intent_text(clause.text)
    synthetic_match = SYNTHETIC_RDS_TEST_RE.search(normalized_text)
    if synthetic_match is not None:
        normalized_clause = PromptClause(text=normalized_text, position=clause.position)
        synthetic_content, synthetic_position = _synthetic_action_content(
            normalized_clause,
            synthetic_start=synthetic_match.start(),
        )
        mapped.append(synthetic_test_action(synthetic_content, synthetic_position))
        return mapped

    mentioned_services = mentioned_integration_services(clause.text)
    matched_slash_registry = False

    for pattern, command in ACTION_PATTERNS:
        match = pattern.search(clause.text)
        if match is None or command in seen_slash:
            continue
        if command == "cli_command":
            if matched_slash_registry:
                continue
            groups = match.groupdict()
            subcmd = groups.get("subcmd") or groups.get("subcmd2")
            if subcmd is None:
                continue
            rest = groups.get("rest") or groups.get("rest2") or ""
            args = f"{subcmd} {rest}".strip() if rest else subcmd
            if subcmd not in seen_slash:
                mapped.append(cli_command_action(args, clause.position + match.start()))
                seen_slash.add(subcmd)
            continue
        if command == "/list integrations" and mentioned_services:
            continue
        mapped.append(slash_action(command, clause.position + match.start()))
        seen_slash.add(command)
        matched_slash_registry = True

    lower = clause.text.lower()
    for service in mentioned_services:
        match = re.search(rf"\b{re.escape(service.replace('_', ' '))}\b", lower)
        position = clause.position + (match.start() if match else 0)
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
            mapped.append(slash_action(slash, position))
            seen_slash.add(slash)

    if mapped:
        return mapped

    provider_switch_action = extract_llm_provider_switch(clause)
    if provider_switch_action is not None:
        mapped.append(provider_switch_action)
        return mapped

    sample_match = SAMPLE_ALERT_RE.search(clause.text)
    if sample_match is not None:
        mapped.append(sample_alert_action("generic", clause.position + sample_match.start()))
        return mapped

    investigation = extract_quoted_investigation_request(clause)
    if investigation is not None:
        mapped.append(investigation)
        return mapped

    implementation = extract_implementation_request(clause)
    if implementation is not None:
        mapped.append(implementation)
        return mapped

    task_cancel = extract_task_cancel_request(clause)
    if task_cancel is not None:
        mapped.append(task_cancel)
        return mapped

    mapped_shell = extract_shell_command(clause)
    if mapped_shell is not None:
        mapped.append(mapped_shell)

    return mapped


def map_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    mapped: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False
    unmatched_clauses: list[PromptClause] = []

    for clause in split_prompt_clauses(message):
        clause_actions = map_clause_actions(
            clause,
            seen_slash=seen_slash,
        )
        if not clause_actions:
            has_unhandled_clause = True
            unmatched_clauses.append(clause)
        mapped.extend(clause_actions)

    has_investigation = any(action.kind == "investigation" for action in mapped)
    if not has_investigation:
        text_level_investigation = extract_quoted_investigation_request_text(message)
        if text_level_investigation is not None:
            mapped.append(text_level_investigation)
            has_investigation = True

    if (
        has_unhandled_clause
        and has_investigation
        and all(
            "investigation" in clause.text.lower()
            or re.match(r'^\s*send\s+it\s+(?:"|\')', clause.text, re.IGNORECASE) is not None
            for clause in unmatched_clauses
        )
    ):
        has_unhandled_clause = False

    return sorted(mapped, key=lambda action: action.position), has_unhandled_clause


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


def plan_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    """Backward-compatible alias for ``map_clause_actions``."""
    return map_clause_actions(clause, seen_slash=seen_slash)


def plan_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    """Backward-compatible alias for ``map_actions_with_unhandled``."""
    return map_actions_with_unhandled(message)


def plan_actions(message: str) -> list[PlannedAction]:
    """Backward-compatible alias for ``map_actions``."""
    return map_actions(message)


def plan_cli_actions(message: str) -> list[str]:
    """Backward-compatible alias for ``map_cli_actions``."""
    return map_cli_actions(message)


def plan_terminal_tasks(message: str) -> list[str]:
    """Backward-compatible alias for ``map_terminal_tasks``."""
    return map_terminal_tasks(message)


__all__ = [
    "map_actions",
    "map_actions_with_unhandled",
    "map_clause_actions",
    "map_cli_actions",
    "map_terminal_tasks",
    "plan_actions",
    "plan_actions_with_unhandled",
    "plan_clause_actions",
    "plan_cli_actions",
    "plan_terminal_tasks",
]
