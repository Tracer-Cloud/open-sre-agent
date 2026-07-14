"""Headless Sentry morning digest via the sentry-summary skill."""

from __future__ import annotations

import logging
from io import StringIO
from typing import Any

from rich.console import Console

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.prompts.prompt_context import DefaultPromptContextProvider
from core.agent_harness.session.integration_resolution import (
    merge_resolved_integrations,
    resolve_and_cache_integrations,
)
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.turn_results import ShellTurnResult
from integrations.sentry.project_scope import (
    apply_sentry_project_scope,
    payload_project_slug,
)
from platform.harness_ports import configured_integration_services
from platform.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_MORNING_DIGEST_BASE_PROMPT = (
    "Sentry morning digest: summarize unresolved Sentry issues from the last 24 hours. "
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


def _dispatch_headless_turn(message: str, payload: AgentPayload) -> ShellTurnResult:
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
    error_reporter = DefaultErrorReporter(logger)
    console = Console(force_terminal=False, file=StringIO())

    agent = HeadlessAgent(
        session=session,
        output=output,
        tools=DefaultToolProvider(session, console, tool_action_logger=logger),
        prompts=DefaultPromptContextProvider(session),
        reasoning=DefaultReasoningClientProvider(
            output=output,
            error_reporter=error_reporter,
            session=session,
        ),
        run_factory=DefaultRunRecordFactory(session),
        accounting=DefaultTurnAccounting(session, message),
        error_reporter=error_reporter,
        gather_enabled=True,
        is_tty=False,
    )
    return agent.dispatch(message)


def run_sentry_morning_digest(payload: AgentPayload) -> str:
    """Run one headless sentry-summary turn and return the assistant report."""
    message = build_morning_digest_prompt(payload)
    result = _dispatch_headless_turn(message, payload)
    report = (result.assistant_response_text or result.action_result.response_text).strip()
    if not result.answered or not report:
        raise RuntimeError(
            "Sentry morning digest failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["build_morning_digest_prompt", "run_sentry_morning_digest"]
