"""Plan deterministic actions from natural-language REPL input."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.cli.interactive_shell.intent.intent_parser import (
    ACTION_PATTERNS,
    INTEGRATION_CAPABILITY_RE,
    INTEGRATION_CONFIG_DETAIL_RE,
    INTEGRATION_DETAIL_RE,
    SAMPLE_ALERT_RE,
    SYNTHETIC_RDS_TEST_RE,
    cli_command_action,
    extract_implementation_request,
    extract_llm_provider_switch,
    extract_shell_command,
    extract_task_cancel_request,
    normalize_intent_text,
    sample_alert_action,
    slash_action,
    split_prompt_clauses,
    synthetic_test_action,
)
from app.cli.interactive_shell.intent.interaction_models import PlannedAction, PromptClause
from app.cli.interactive_shell.intent.terminal_intent import mentioned_integration_services
from app.cli.interactive_shell.routing.llm_synthetic_scenario_resolver import (
    resolve_synthetic_scenario_with_llm,
)

# Deterministic match for an already-canonical scenario ID like "003-storage-full".
# A regex is appropriate here because the format is exact and unambiguous; no
# inference is needed. Anything looser ("003", "the storage full one") is
# resolved by the LLM-backed scenario resolver below.
_SYNTHETIC_SCENARIO_ID_RE = re.compile(
    r"\b(?P<scenario>\d{3}-[a-z0-9][a-z0-9-]*)\b",
    re.IGNORECASE,
)

# Numeric scenario hint, e.g. "16", "016", "test 7", "scenario #3". Bounded to
# 1-4 digits to avoid matching long unrelated numeric tokens (timestamps, IDs).
# The capture group is the raw user token; we left-pad to 3 digits when
# comparing against the directory prefix.
_SYNTHETIC_NUMERIC_HINT_RE = re.compile(r"\b(?P<num>\d{1,4})\b")

DEFAULT_SYNTHETIC_SCENARIO = "001-replication-lag"

# Sentinel content emitted when the user pointed at a specific (non-existent)
# scenario. The planner threads this through to the executor instead of silently
# falling back to ``DEFAULT_SYNTHETIC_SCENARIO``, so the user sees an explicit
# "no such scenario" error rather than the wrong test getting launched.
SYNTHETIC_UNKNOWN_PREFIX = "rds_postgres:unknown:"

# ``parents[4]`` is the repo root. The earlier ``parents[3]`` survived from
# when this module lived in the flat ``interactive_shell/`` layout, and after
# the move into ``orchestration/`` it silently resolved to ``app/`` —
# ``_list_rds_postgres_scenarios()`` then always returned an empty tuple, the
# LLM scenario resolver received an empty allowlist, and every fuzzy
# synthetic-test request fell back to ``DEFAULT_SYNTHETIC_SCENARIO``
# regardless of what the user typed. Counting parents from this file:
#   parents[0] orchestration / parents[1] interactive_shell / parents[2] cli
#   parents[3] app           / parents[4] <repo root>
_RDS_POSTGRES_SUITE_DIR = (
    Path(__file__).resolve().parents[4] / "tests" / "synthetic" / "rds_postgres"
)


@lru_cache(maxsize=1)
def _list_rds_postgres_scenarios() -> tuple[str, ...]:
    """Enumerate available RDS Postgres synthetic scenario directory names.

    Returns a tuple of names like ("001-replication-lag", "002-connection-exhaustion", ...).
    Cached because the suite layout is static at runtime. Returns an empty
    tuple when the directory is missing (trimmed installs, packaging).

    The list is passed to the LLM resolver as a strict allowlist so the model
    can only ever return a real scenario.
    """
    if not _RDS_POSTGRES_SUITE_DIR.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in _RDS_POSTGRES_SUITE_DIR.iterdir()
            if entry.is_dir()
            and len(entry.name) >= 5
            and entry.name[:3].isdigit()
            and entry.name[3] == "-"
        )
    )


def _detect_unresolved_numeric_hint(
    text: str,
    scenarios: tuple[str, ...],
) -> str | None:
    """Return the raw numeric token the user typed when it doesn't match any scenario.

    The user typing ``test 16`` when the suite only ships ``000``–``015`` is a
    different failure mode than ``run a synthetic test`` (no scenario given at
    all): the first deserves an explicit "no such scenario" error rather than a
    silent fallback to ``DEFAULT_SYNTHETIC_SCENARIO``. We detect this by
    scanning for a 1–4 digit token in *text* and checking the left-padded
    3-digit prefix against the allowlist.

    Returns the raw user-typed digits (e.g. ``"16"``) so the caller can quote
    them verbatim in the error message, or ``None`` when every numeric token in
    the text already matches a known scenario (or none was found at all).
    """
    for match in _SYNTHETIC_NUMERIC_HINT_RE.finditer(text):
        raw = match.group("num")
        padded = raw.zfill(3) if len(raw) <= 3 else raw
        if not any(name.startswith(f"{padded}-") for name in scenarios):
            return raw
    return None


def _synthetic_action_content(clause: PromptClause, *, synthetic_start: int) -> tuple[str, int]:
    """Resolve the scenario the user asked for to a ``rds_postgres:<id>`` token.

    Resolution order:
    1. A canonical full ID (``003-storage-full``) is taken verbatim — no LLM
       needed when the user already typed the directory name.
    2. Otherwise an LLM intent classifier picks the scenario from the live
       allowlist. This handles bare numbers ("003", "test 3"), descriptive
       phrases ("the storage full one", "cpu saturation"), and typos.
    3. If the LLM declines AND the user pointed at a specific numeric ID that
       doesn't exist in the allowlist, emit a ``SYNTHETIC_UNKNOWN_PREFIX``
       sentinel so the executor can surface a tailored "no such scenario"
       error rather than silently running ``DEFAULT_SYNTHETIC_SCENARIO``.
    4. Otherwise (no specific hint, or LLM unavailable on a generic request),
       fall back to the default scenario so a bare ``run a synthetic test``
       still launches something useful.
    """
    full_match = _SYNTHETIC_SCENARIO_ID_RE.search(clause.text)
    if full_match is not None:
        scenario_id = full_match.group("scenario").lower()
        return (
            f"rds_postgres:{scenario_id}",
            clause.position + full_match.start("scenario"),
        )

    scenarios = _list_rds_postgres_scenarios()
    resolved = resolve_synthetic_scenario_with_llm(clause.text, scenarios)
    if resolved is not None:
        return (
            f"rds_postgres:{resolved}",
            clause.position + synthetic_start,
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


def plan_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    planned: list[PlannedAction] = []
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
                planned.append(cli_command_action(args, clause.position + match.start()))
                seen_slash.add(subcmd)
            continue
        if command == "/list integrations" and mentioned_services:
            continue
        planned.append(slash_action(command, clause.position + match.start()))
        seen_slash.add(command)
        matched_slash_registry = True

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

    # Normalize only for SYNTHETIC_RDS_TEST_RE to handle typos like "syntehtic" → "synthetic".
    # Using normalized text only here avoids false positives in shell command extraction.
    normalized_text = normalize_intent_text(clause.text)
    synthetic_match = SYNTHETIC_RDS_TEST_RE.search(normalized_text)
    if synthetic_match is not None:
        normalized_clause = PromptClause(text=normalized_text, position=clause.position)
        synthetic_content, synthetic_position = _synthetic_action_content(
            normalized_clause,
            synthetic_start=synthetic_match.start(),
        )
        planned.append(synthetic_test_action(synthetic_content, synthetic_position))
        return planned

    sample_match = SAMPLE_ALERT_RE.search(clause.text)
    if sample_match is not None:
        planned.append(sample_alert_action("generic", clause.position + sample_match.start()))
        return planned

    implementation = extract_implementation_request(clause)
    if implementation is not None:
        planned.append(implementation)
        return planned

    task_cancel = extract_task_cancel_request(clause)
    if task_cancel is not None:
        planned.append(task_cancel)
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
    """Return safe read-only slash commands and CLI commands requested by a natural-language turn."""
    return [
        action.content
        for action in plan_actions(message)
        if action.kind in ("slash", "cli_command")
    ]


def plan_terminal_tasks(message: str) -> list[str]:
    """Return a test-friendly view of all deterministic terminal tasks."""
    return [action.kind for action in plan_actions(message)]


__all__ = [
    "DEFAULT_SYNTHETIC_SCENARIO",
    "SYNTHETIC_UNKNOWN_PREFIX",
    "plan_actions",
    "plan_actions_with_unhandled",
    "plan_cli_actions",
    "plan_clause_actions",
    "plan_terminal_tasks",
]
