"""Declarative integration-detail mapping policy for deterministic routing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    INTEGRATION_CAPABILITY_RE,
    INTEGRATION_CONFIG_DETAIL_RE,
    INTEGRATION_DETAIL_RE,
    slash_action,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)


@dataclass(frozen=True)
class IntegrationDetailPolicy:
    """Configures context windows and regex gates for integration detail routing."""

    name: str
    detail_context_before: int
    detail_context_after: int
    config_context_before: int
    config_context_after: int
    detail_pattern: re.Pattern[str]
    config_detail_pattern: re.Pattern[str]
    capability_pattern: re.Pattern[str]


DEFAULT_INTEGRATION_DETAIL_POLICIES: tuple[IntegrationDetailPolicy, ...] = (
    IntegrationDetailPolicy(
        name="service_detail_with_config_intent",
        detail_context_before=80,
        detail_context_after=120,
        config_context_before=30,
        config_context_after=70,
        detail_pattern=INTEGRATION_DETAIL_RE,
        config_detail_pattern=INTEGRATION_CONFIG_DETAIL_RE,
        capability_pattern=INTEGRATION_CAPABILITY_RE,
    ),
)


def _service_position(clause_text: str, service: str) -> int:
    service_match = re.search(
        rf"\b{re.escape(service.replace('_', ' '))}\b",
        clause_text.lower(),
    )
    return service_match.start() if service_match else 0


def _context_window(text: str, *, center: int, before: int, after: int) -> str:
    start = max(0, center - before)
    end = min(len(text), center + after)
    return text[start:end]


def map_integration_detail_actions(
    clause: PromptClause,
    *,
    mentioned_services: list[str],
    seen_slash: set[str],
    policies: tuple[IntegrationDetailPolicy, ...] = DEFAULT_INTEGRATION_DETAIL_POLICIES,
) -> list[PlannedAction]:
    """Map integration-detail natural language to `/integrations show <service>` actions."""
    mapped: list[PlannedAction] = []
    for service in mentioned_services:
        service_offset = _service_position(clause.text, service)
        absolute_position = clause.position + service_offset
        slash = f"/integrations show {service}"
        if slash in seen_slash:
            continue

        for policy in policies:
            detail_window = _context_window(
                clause.text,
                center=service_offset,
                before=policy.detail_context_before,
                after=policy.detail_context_after,
            )
            config_window = _context_window(
                clause.text,
                center=service_offset,
                before=policy.config_context_before,
                after=policy.config_context_after,
            )
            wants_detail = policy.detail_pattern.search(detail_window) is not None
            wants_config = policy.config_detail_pattern.search(config_window) is not None
            capability_only = policy.capability_pattern.search(detail_window) is not None
            if wants_detail and wants_config and not capability_only:
                mapped.append(slash_action(slash, absolute_position))
                seen_slash.add(slash)
                break
    return mapped


__all__ = [
    "DEFAULT_INTEGRATION_DETAIL_POLICIES",
    "IntegrationDetailPolicy",
    "map_integration_detail_actions",
]
