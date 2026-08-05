"""Headless Sentry morning digest via the sentry-summary skill."""

from __future__ import annotations

import logging
from typing import Any

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.session.integration_resolution import (
    merge_resolved_integrations,
    resolve_and_cache_integrations,
)
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.turn_results import TurnResult
from integrations.sentry.project_scope import (
    apply_sentry_project_scope,
    payload_project_slug,
)
from platform.harness_ports import configured_integration_services
from platform.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_MORNING_DIGEST_BASE_PROMPT = (
    "Sentry morning digest: summarize unresolved Sentry issues from the last 24 hours "
    "and include uptime status from the watch transition log. "
    "Follow the sentry-summary skill workflow."
)


def build_morning_digest_prompt(payload: AgentPayload) -> str:
    """Build the fixed headless prompt for scheduled/on-demand morning digests."""
    prompt = _MORNING_DIGEST_BASE_PROMPT
    project = payload_project_slug(payload)
    if project:
        prompt = f"{prompt} Project scope is fixed to {project!r} for this run."
    return prompt


def _apply_digest_project_scope(session: Any, payload: AgentPayload) -> None:
    """Pin ``project_slug`` on the session Sentry integration before tool calls."""
    project = payload_project_slug(payload)
    if not project:
        return
    resolved = resolve_and_cache_integrations(session)
    scoped = apply_sentry_project_scope(resolved, project)
    session.resolved_integrations_cache = merge_resolved_integrations(
        session.resolved_integrations_cache,
        {"sentry": scoped.get("sentry", {})},
    )


def _require_sentry_configured() -> None:
    if "sentry" not in configured_integration_services():
        raise RuntimeError(
            "Sentry is not configured. Run `opensre integrations setup` and verify "
            "with `opensre integrations verify sentry` before scheduling a digest."
        )


def _dispatch_headless_turn(message: str, payload: AgentPayload) -> TurnResult:
    _require_sentry_configured()

    harness = AgentHarness(
        HarnessConfig(
            load_env=True,
            hydrate_integrations=True,
            warm_integrations=True,
            persistent_tasks=False,
            open_storage=False,
        )
    )
    startup = harness.startup()
    session = startup.session
    _apply_digest_project_scope(session, payload)
    output = BufferOutputSink()
    agent = build_default_headless_agent(
        session=session,
        output=output,
        logger=logger,
        message=message,
        gather_enabled=True,
        is_tty=False,
    )
    harness.attach_agent(agent)
    return harness.dispatch_message(message)


def run_sentry_morning_digest(payload: AgentPayload) -> str:
    """Run one headless sentry-summary turn and return the assistant report."""
    message = build_morning_digest_prompt(payload)
    result = _dispatch_headless_turn(message, payload)
    report = result.primary_response_text
    if not result.answered:
        if report:
            raise RuntimeError(f"Sentry morning digest failed: {report}")
        raise RuntimeError(
            "Sentry morning digest failed: the reasoning client did not produce a response."
        )
    if not report:
        raise RuntimeError(
            "Sentry morning digest failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["build_morning_digest_prompt", "run_sentry_morning_digest"]
