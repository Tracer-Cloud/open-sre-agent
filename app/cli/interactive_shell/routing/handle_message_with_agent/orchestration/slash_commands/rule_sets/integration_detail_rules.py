"""Declarative rule pack for integration-detail mapping."""

from __future__ import annotations

import re

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    INTEGRATION_CAPABILITY_RE,
    INTEGRATION_CONFIG_DETAIL_RE,
    INTEGRATION_DETAIL_RE,
    mentioned_integration_services,
    slash_action,
)

from ..rule_types import INTEGRATION_DETAIL_WINDOW, INTEGRATION_WINDOW, ClauseRuleContext


def apply_integration_detail_rule(ctx: ClauseRuleContext) -> bool:
    lower = ctx.clause.text.lower()
    matched = False
    for service in mentioned_integration_services(ctx.clause.text):
        match = re.search(rf"\b{re.escape(service.replace('_', ' '))}\b", lower)
        if match is None:
            continue

        relative_position = match.start()
        window = INTEGRATION_WINDOW.slice(ctx.clause.text, anchor_start=relative_position)
        detail_window = INTEGRATION_DETAIL_WINDOW.slice(
            ctx.clause.text, anchor_start=relative_position
        )

        slash = f"/integrations show {service}"
        wants_config_detail = INTEGRATION_CONFIG_DETAIL_RE.search(detail_window) is not None
        capability_only = INTEGRATION_CAPABILITY_RE.search(window) is not None
        if (
            slash not in ctx.seen_slash
            and INTEGRATION_DETAIL_RE.search(window)
            and wants_config_detail
            and not capability_only
        ):
            absolute_position = ctx.clause.position + relative_position
            ctx.mapped.append(slash_action(slash, absolute_position))
            ctx.seen_slash.add(slash)
            ctx.trace.append(f"integration_detail:{service}")
            matched = True
    return matched
