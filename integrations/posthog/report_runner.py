"""Headless PostHog per-metric report via the posthog-summary skill.

Mirrors :mod:`integrations.sentry.morning_digest_runner`: run one headless
agent turn driven by the ``posthog-summary`` skill and return the assistant
report text. Used by the ``opensre posthog report`` command and the scheduled
delivery path (issue #3824).
"""

from __future__ import annotations

import logging
from io import StringIO

from rich.console import Console

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.prompts.prompt_context import DefaultPromptContextProvider
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.turn_results import TurnResult
from integrations.posthog.report_prerequisites import (
    posthog_not_configured_hint,
    posthog_report_available,
)
from platform.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_REPORT_BASE_PROMPT = (
    "PostHog analytics report: produce a per-metric summary report of PostHog "
    "product analytics. Follow the posthog-summary skill workflow."
)

_DEFAULT_STATS_PERIOD = "7d"


def _payload_stats_period(payload: AgentPayload) -> str:
    period = str(payload.get("stats_period") or "").strip()
    return period or _DEFAULT_STATS_PERIOD


def _payload_metrics(payload: AgentPayload) -> str:
    return str(payload.get("metrics") or "").strip()


def build_report_prompt(payload: AgentPayload) -> str:
    """Build the fixed headless prompt for scheduled/on-demand metric reports."""
    prompt = f"{_REPORT_BASE_PROMPT} Use a {_payload_stats_period(payload)} window."
    metrics = _payload_metrics(payload)
    if metrics:
        prompt = f"{prompt} Focus on these metrics: {metrics}."
    return prompt


def _require_posthog_configured() -> None:
    if posthog_report_available():
        return
    raise RuntimeError(posthog_not_configured_hint())


def _dispatch_headless_turn(message: str) -> TurnResult:
    _require_posthog_configured()

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


def run_posthog_report(payload: AgentPayload) -> str:
    """Run one headless posthog-summary turn and return the assistant report."""
    message = build_report_prompt(payload)
    result = _dispatch_headless_turn(message)
    report = (result.assistant_response_text or result.action_result.response_text).strip()
    if not result.answered or not report:
        raise RuntimeError(
            "PostHog report failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["build_report_prompt", "run_posthog_report"]
