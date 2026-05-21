"""Ordered execution runner for declarative deterministic mapping rules."""

from __future__ import annotations

from dataclasses import dataclass

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)

from .rule_sets.fallback_rules import (
    apply_implementation_rule,
    apply_investigation_rule,
    apply_provider_switch_rule,
    apply_sample_alert_rule,
    apply_shell_rule,
    apply_task_cancel_rule,
)
from .rule_sets.integration_detail_rules import apply_integration_detail_rule
from .rule_sets.registry_rules import (
    apply_registry_rule,
    apply_synthetic_rule,
    build_normalized_text,
)
from .rule_types import ClauseRuleContext, ClauseRuleSpec


@dataclass(frozen=True)
class ClauseMappingResult:
    actions: list[PlannedAction]
    trace: tuple[str, ...]


RULE_PRECEDENCE: tuple[ClauseRuleSpec, ...] = (
    ClauseRuleSpec(name="synthetic_suite", evaluator=apply_synthetic_rule),
    ClauseRuleSpec(name="registry_commands", evaluator=apply_registry_rule),
    ClauseRuleSpec(name="integration_details", evaluator=apply_integration_detail_rule),
    ClauseRuleSpec(name="fallback_provider_switch", evaluator=apply_provider_switch_rule),
    ClauseRuleSpec(name="fallback_sample_alert", evaluator=apply_sample_alert_rule),
    ClauseRuleSpec(name="fallback_investigation", evaluator=apply_investigation_rule),
    ClauseRuleSpec(name="fallback_implementation", evaluator=apply_implementation_rule),
    ClauseRuleSpec(name="fallback_task_cancel", evaluator=apply_task_cancel_rule),
    ClauseRuleSpec(name="fallback_shell", evaluator=apply_shell_rule),
)


_SHORT_CIRCUIT_RULES = frozenset(
    {
        "synthetic_suite",
        "fallback_provider_switch",
        "fallback_sample_alert",
        "fallback_investigation",
        "fallback_implementation",
        "fallback_task_cancel",
    }
)


def _should_skip(rule_name: str, ctx: ClauseRuleContext) -> bool:
    if rule_name.startswith("fallback_"):
        return bool(ctx.mapped)
    return False


def run_clause_rules(clause: PromptClause, *, seen_slash: set[str]) -> ClauseMappingResult:
    ctx = ClauseRuleContext(
        clause=clause,
        normalized_text=build_normalized_text(clause.text),
        seen_slash=seen_slash,
    )
    for rule in RULE_PRECEDENCE:
        if _should_skip(rule.name, ctx):
            continue
        matched = rule.evaluator(ctx)
        if not matched:
            continue
        if rule.name in _SHORT_CIRCUIT_RULES:
            break
    return ClauseMappingResult(actions=list(ctx.mapped), trace=tuple(ctx.trace))
