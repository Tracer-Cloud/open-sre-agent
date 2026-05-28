"""Declarative rule pack for synthetic and slash/CLI registry mapping."""

from __future__ import annotations

import re

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    ACTION_PATTERNS,
    SYNTHETIC_RDS_TEST_RE,
    cli_command_action,
    normalize_intent_text,
    slash_action,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    DEFAULT_SYNTHETIC_SCENARIO,
    SYNTHETIC_UNKNOWN_PREFIX,
    list_rds_postgres_scenarios,
)

from ..rule_types import ClauseRuleContext

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


def _synthetic_action_content(clause_text: str, *, synthetic_start: int) -> tuple[str, int]:
    if _SYNTHETIC_ALL_RE.search(clause_text) is not None:
        return ("rds_postgres:all", synthetic_start)

    full_match = _SYNTHETIC_SCENARIO_ID_RE.search(clause_text)
    if full_match is not None:
        scenario_id = full_match.group("scenario").lower()
        return (f"rds_postgres:{scenario_id}", full_match.start("scenario"))

    scenarios = list_rds_postgres_scenarios()
    resolved = _resolve_numeric_hint(clause_text, scenarios)
    if resolved is not None:
        scenario_id, match_start = resolved
        return (f"rds_postgres:{scenario_id}", match_start)

    unresolved_hint = _detect_unresolved_numeric_hint(clause_text, scenarios)
    if unresolved_hint is not None:
        return (f"{SYNTHETIC_UNKNOWN_PREFIX}{unresolved_hint}", synthetic_start)

    return (f"rds_postgres:{DEFAULT_SYNTHETIC_SCENARIO}", synthetic_start)


def apply_synthetic_rule(ctx: ClauseRuleContext) -> bool:
    synthetic_match = SYNTHETIC_RDS_TEST_RE.search(ctx.normalized_text)
    if synthetic_match is None:
        return False
    synthetic_content, relative_position = _synthetic_action_content(
        ctx.normalized_text,
        synthetic_start=synthetic_match.start(),
    )
    ctx.mapped.append(
        synthetic_test_action(synthetic_content, ctx.clause.position + relative_position)
    )
    ctx.trace.append("synthetic_suite")
    return True


def apply_registry_rule(ctx: ClauseRuleContext) -> bool:
    mentioned_services = mentioned_integration_services(ctx.clause.text)
    for pattern, command in ACTION_PATTERNS:
        match = pattern.search(ctx.clause.text)
        if match is None or command in ctx.seen_slash:
            continue
        if command == "cli_command":
            if ctx.matched_slash_registry:
                continue
            groups = match.groupdict()
            subcmd = groups.get("subcmd") or groups.get("subcmd2")
            if subcmd is None:
                continue
            rest = groups.get("rest") or groups.get("rest2") or ""
            args = f"{subcmd} {rest}".strip() if rest else subcmd
            if subcmd not in ctx.seen_slash:
                ctx.mapped.append(cli_command_action(args, ctx.clause.position + match.start()))
                ctx.seen_slash.add(subcmd)
                ctx.trace.append("cli_command_registry")
            continue
        if command == "/list integrations" and mentioned_services:
            continue
        ctx.mapped.append(slash_action(command, ctx.clause.position + match.start()))
        ctx.seen_slash.add(command)
        ctx.matched_slash_registry = True
        ctx.trace.append(f"slash_registry:{command}")
    return bool(ctx.mapped)


def build_normalized_text(text: str) -> str:
    return normalize_intent_text(text)


from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (  # noqa: E402
    mentioned_integration_services,
    synthetic_test_action,
)
