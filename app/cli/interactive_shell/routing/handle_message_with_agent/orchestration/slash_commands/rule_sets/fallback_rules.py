"""Declarative rule pack for non-registry fallback action extraction."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    SAMPLE_ALERT_RE,
    extract_implementation_request,
    extract_llm_provider_switch,
    extract_quoted_investigation_request,
    extract_shell_command,
    extract_task_cancel_request,
    sample_alert_action,
)

from ..rule_types import ClauseRuleContext


def apply_provider_switch_rule(ctx: ClauseRuleContext) -> bool:
    provider_switch_action = extract_llm_provider_switch(ctx.clause)
    if provider_switch_action is None:
        return False
    ctx.mapped.append(provider_switch_action)
    ctx.trace.append("fallback_provider_switch")
    return True


def apply_sample_alert_rule(ctx: ClauseRuleContext) -> bool:
    sample_match = SAMPLE_ALERT_RE.search(ctx.clause.text)
    if sample_match is None:
        return False
    ctx.mapped.append(sample_alert_action("generic", ctx.clause.position + sample_match.start()))
    ctx.trace.append("fallback_sample_alert")
    return True


def apply_investigation_rule(ctx: ClauseRuleContext) -> bool:
    investigation = extract_quoted_investigation_request(ctx.clause)
    if investigation is None:
        return False
    ctx.mapped.append(investigation)
    ctx.trace.append("fallback_investigation")
    return True


def apply_implementation_rule(ctx: ClauseRuleContext) -> bool:
    implementation = extract_implementation_request(ctx.clause)
    if implementation is None:
        return False
    ctx.mapped.append(implementation)
    ctx.trace.append("fallback_implementation")
    return True


def apply_task_cancel_rule(ctx: ClauseRuleContext) -> bool:
    task_cancel = extract_task_cancel_request(ctx.clause)
    if task_cancel is None:
        return False
    ctx.mapped.append(task_cancel)
    ctx.trace.append("fallback_task_cancel")
    return True


def apply_shell_rule(ctx: ClauseRuleContext) -> bool:
    mapped_shell = extract_shell_command(ctx.clause)
    if mapped_shell is None:
        return False
    ctx.mapped.append(mapped_shell)
    ctx.trace.append("fallback_shell")
    return True
