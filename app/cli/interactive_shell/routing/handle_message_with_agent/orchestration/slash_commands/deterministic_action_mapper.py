"""Deterministic mapper from natural language to terminal actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
    ParseError,
    PlannerUnavailable,
    PolicyError,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    ACTION_PATTERNS,
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
    ActionKind,
    PlannedAction,
    PromptClause,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.policy_engine import (
    MatchPhase,
    run_first_match,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.integration_detail_policy import (
    map_integration_detail_actions,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.mapper_runner import (
    run_clause_rules,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.synthetic_resolution import (
    resolve_synthetic_action_content,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    SYNTHETIC_UNKNOWN_PREFIX,
    list_rds_postgres_scenarios,
)
from app.cli.interactive_shell.routing.policy_tags import (
    ClauseMappingPhaseTag,
    DeterministicMapperPolicyTag,
)
from app.cli.support.exception_reporting import report_exception


@dataclass(frozen=True)
class DeterministicMappingResult:
    """Structured deterministic mapping result with policy trace metadata."""

    actions: tuple[PlannedAction, ...]
    has_unhandled_clause: bool
    applied_policies: tuple[DeterministicMapperPolicyTag, ...]
    phase_trace: tuple[ClausePhaseTraceEntry, ...] = ()


@dataclass(frozen=True)
class ClausePhaseTraceEntry:
    """Typed trace entry emitted by one matched clause-mapping phase."""

    phase: ClauseMappingPhaseTag
    emitted_kinds: tuple[ActionKind, ...]
    clause_position: int


def _map_synthetic_clause(clause: PromptClause, _seen_slash: set[str]) -> list[PlannedAction]:
    normalized_text = normalize_intent_text(clause.text)
    synthetic_match = SYNTHETIC_RDS_TEST_RE.search(normalized_text)
    if synthetic_match is None:
        return []
    normalized_clause = PromptClause(text=normalized_text, position=clause.position)
    synthetic_content, synthetic_position = resolve_synthetic_action_content(
        normalized_clause,
        synthetic_start=synthetic_match.start(),
    )
    return [synthetic_test_action(synthetic_content, synthetic_position)]


def _map_registry_actions(
    clause: PromptClause,
    *,
    mentioned_services: list[str],
    seen_slash: set[str],
) -> list[PlannedAction]:
    mapped: list[PlannedAction] = []
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
    return mapped


def _map_fallback_actions(clause: PromptClause, _seen_slash: set[str]) -> list[PlannedAction]:
    provider_switch_action = extract_llm_provider_switch(clause)
    if provider_switch_action is not None:
        return [provider_switch_action]

    sample_match = SAMPLE_ALERT_RE.search(clause.text)
    if sample_match is not None:
        return [sample_alert_action("generic", clause.position + sample_match.start())]

    investigation = extract_quoted_investigation_request(clause)
    if investigation is not None:
        return [investigation]

    implementation = extract_implementation_request(clause)
    if implementation is not None:
        return [implementation]

    task_cancel = extract_task_cancel_request(clause)
    if task_cancel is not None:
        return [task_cancel]

    mapped_shell = extract_shell_command(clause)
    return [mapped_shell] if mapped_shell is not None else []


def _map_registry_and_integration_detail(
    clause: PromptClause,
    seen_slash: set[str],
) -> list[PlannedAction]:
    services = mentioned_integration_services(clause.text)
    registry_mapped = _map_registry_actions(
        clause,
        mentioned_services=services,
        seen_slash=seen_slash,
    )
    detail_mapped = map_integration_detail_actions(
        clause,
        mentioned_services=services,
        seen_slash=seen_slash,
    )
    if registry_mapped or detail_mapped:
        return [*registry_mapped, *detail_mapped]
    return []


_CLAUSE_MAPPING_PHASES: tuple[
    MatchPhase[tuple[PromptClause, set[str]], list[PlannedAction], ClauseMappingPhaseTag], ...
] = (
    MatchPhase(ClauseMappingPhaseTag.SYNTHETIC, lambda ctx: _map_synthetic_clause(ctx[0], ctx[1])),
    MatchPhase(
        ClauseMappingPhaseTag.REGISTRY_AND_INTEGRATION_DETAIL,
        lambda ctx: _map_registry_and_integration_detail(ctx[0], ctx[1]),
    ),
    MatchPhase(
        ClauseMappingPhaseTag.FALLBACK_EXTRACTORS,
        lambda ctx: _map_fallback_actions(ctx[0], ctx[1]),
    ),
)


def map_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    mapped, _phase_name = _map_clause_actions_with_phase_internal(
        clause,
        seen_slash=seen_slash,
    )
    return mapped


def map_clause_actions_with_phase(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> tuple[list[PlannedAction], ClauseMappingPhaseTag | None]:
    # Preserve map_clause_actions as a monkeypatch seam used by degrade-path tests.
    # If callers patch map_clause_actions to raise, this wrapper should raise too.
    original_seen_slash = set(seen_slash)
    mapped = map_clause_actions(clause, seen_slash=seen_slash)
    if not mapped:
        return [], None

    replay_mapped, replay_phase = _map_clause_actions_with_phase_internal(
        clause,
        seen_slash=original_seen_slash,
    )
    if replay_mapped == mapped:
        return mapped, replay_phase
    return mapped, None


def _map_clause_actions_with_phase_internal(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> tuple[list[PlannedAction], ClauseMappingPhaseTag | None]:
    mapped, phase = run_first_match(
        (clause, seen_slash),
        _CLAUSE_MAPPING_PHASES,
        is_match=bool,
    )
    if mapped is None:
        return [], None
    return mapped, phase


_INVESTIGATION_ONLY_UNHANDLED_ALLOW_RE = re.compile(
    r'^\s*send\s+it\s+(?:"|\'|`)',
    re.IGNORECASE,
)


def _clause_is_investigation_only_follow_up(clause: PromptClause) -> bool:
    text = clause.text.lower()
    return (
        "investigation" in text
        or _INVESTIGATION_ONLY_UNHANDLED_ALLOW_RE.match(clause.text) is not None
    )


def _apply_text_level_investigation_policy(
    message: str,
    mapped: list[PlannedAction],
    applied_policies: list[DeterministicMapperPolicyTag],
) -> bool:
    has_investigation = any(action.kind == "investigation" for action in mapped)
    if has_investigation:
        return True
    text_level_investigation = extract_quoted_investigation_request_text(message)
    if text_level_investigation is None:
        return False
    mapped.append(text_level_investigation)
    applied_policies.append(DeterministicMapperPolicyTag.TEXT_LEVEL_INVESTIGATION_ADDED)
    return True


def _apply_investigation_only_unhandled_waiver_policy(
    *,
    has_unhandled_clause: bool,
    has_investigation: bool,
    unmatched_clauses: list[PromptClause],
    applied_policies: list[DeterministicMapperPolicyTag],
) -> bool:
    if not has_unhandled_clause or not has_investigation:
        return has_unhandled_clause
    if all(_clause_is_investigation_only_follow_up(clause) for clause in unmatched_clauses):
        applied_policies.append(DeterministicMapperPolicyTag.INVESTIGATION_ONLY_UNHANDLED_WAIVED)
        return False
    return has_unhandled_clause


def map_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    result = map_actions_result(message)
    return list(result.actions), result.has_unhandled_clause


def map_actions_result(message: str) -> DeterministicMappingResult:
    mapped: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False
    unmatched_clauses: list[PromptClause] = []
    applied_policies: list[DeterministicMapperPolicyTag] = []
    phase_trace: list[ClausePhaseTraceEntry] = []

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
            clause_actions, phase_name = map_clause_actions_with_phase(
                clause,
                seen_slash=seen_slash,
            )
            if phase_name is not None:
                phase_trace.append(
                    ClausePhaseTraceEntry(
                        phase=phase_name,
                        emitted_kinds=tuple(action.kind for action in clause_actions),
                        clause_position=clause.position,
                    )
                )
            if not clause_actions:
                has_unhandled_clause = True
                unmatched_clauses.append(clause)
                if DeterministicMapperPolicyTag.UNHANDLED_CLAUSE_DETECTED not in applied_policies:
                    applied_policies.append(DeterministicMapperPolicyTag.UNHANDLED_CLAUSE_DETECTED)
            mapped.extend(clause_actions)
    except Exception as exc:
        report_exception(
            exc,
            context="interactive_shell.routing.mapper.map_clause_actions",
            extra={"degrade_reason_tag": PolicyError.reason_tag, "text_length": len(message)},
        )
        raise PolicyError("Failed to apply routing policy to one or more prompt clauses.") from exc

    try:
        has_investigation = _apply_text_level_investigation_policy(
            message, mapped, applied_policies
        )
        has_unhandled_clause = _apply_investigation_only_unhandled_waiver_policy(
            has_unhandled_clause=has_unhandled_clause,
            has_investigation=has_investigation,
            unmatched_clauses=unmatched_clauses,
            applied_policies=applied_policies,
        )

        return DeterministicMappingResult(
            actions=tuple(sorted(mapped, key=lambda action: action.position)),
            has_unhandled_clause=has_unhandled_clause,
            applied_policies=tuple(applied_policies),
            phase_trace=tuple(phase_trace),
        )
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


def map_actions_with_trace(
    message: str,
) -> tuple[list[PlannedAction], bool, list[dict[str, Any]]]:
    """Backward-compatible mapping API that also emits per-clause trace metadata."""

    mapped: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False
    unmatched_clauses: list[PromptClause] = []
    trace: list[dict[str, Any]] = []

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
            clause_result = run_clause_rules(clause, seen_slash=seen_slash)
            clause_actions = clause_result.actions
            if clause_result.trace:
                trace.append(
                    {
                        "clause_text": clause.text,
                        "clause_position": clause.position,
                        "rules": list(clause_result.trace),
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
        has_investigation = _apply_text_level_investigation_policy(message, mapped, [])
        has_unhandled_clause = _apply_investigation_only_unhandled_waiver_policy(
            has_unhandled_clause=has_unhandled_clause,
            has_investigation=has_investigation,
            unmatched_clauses=unmatched_clauses,
            applied_policies=[],
        )
        return sorted(mapped, key=lambda action: action.position), has_unhandled_clause, trace
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


def map_cli_actions(message: str) -> list[str]:
    """Return safe read-only slash commands and CLI commands requested by a natural-language turn."""
    actions = map_actions_result(message).actions
    return [action.content for action in actions if action.kind in ("slash", "cli_command")]


def map_terminal_tasks(message: str) -> list[str]:
    """Return a test-friendly view of all deterministic terminal tasks."""
    return [action.kind for action in map_actions_result(message).actions]


__all__ = [
    "DeterministicMapperPolicyTag",
    "ClausePhaseTraceEntry",
    "DeterministicMappingResult",
    "map_actions_result",
    "map_actions_with_trace",
    "map_actions_with_unhandled",
    "map_clause_actions",
    "map_clause_actions_with_phase",
    "map_cli_actions",
    "map_terminal_tasks",
    "SYNTHETIC_UNKNOWN_PREFIX",
    "list_rds_postgres_scenarios",
]
