"""Headless PostHog per-metric report via the posthog-summary skill.

Mirrors :mod:`integrations.sentry.morning_digest_runner`: run one headless
agent turn driven by the ``posthog-summary`` skill and return the assistant
report text. Used by the ``opensre posthog report`` command and the scheduled
delivery path (issue #3824).
"""

from __future__ import annotations

import logging

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.evidence_driver import MAX_REPORT_GATHER_ITERATIONS
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.turn_results import TurnResult
from integrations.posthog.report_prerequisites import (
    DEFAULT_POSTHOG_PERIOD,
    posthog_not_configured_hint,
    posthog_report_available,
)
from platform.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_REPORT_BASE_PROMPT = (
    "PostHog analytics report: produce a per-metric product-analytics pulse "
    "for the team — what moved and why it matters. "
    "Follow the posthog-summary skill workflow."
)


def _payload_stats_period(payload: AgentPayload) -> str:
    period = str(payload.get("stats_period") or "").strip()
    return period or DEFAULT_POSTHOG_PERIOD


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
    agent = build_default_headless_agent(
        session=session,
        output=output,
        logger=logger,
        message=message,
        gather_enabled=True,
        # Schema discover + one HogQL call per metric needs more than the
        # default chat gather budget (4), or later funnel rows never run.
        gather_max_iterations=MAX_REPORT_GATHER_ITERATIONS,
        is_tty=False,
    )
    harness.attach_agent(agent)
    return harness.dispatch_message(message)


def run_posthog_report(payload: AgentPayload) -> str:
    """Run one headless posthog-summary turn and return the assistant report."""
    message = build_report_prompt(payload)
    result = _dispatch_headless_turn(message)
    report = result.primary_response_text
    if not result.answered:
        # Billing/auth failures often leave a useful message on the action path
        # without an ``llm_run`` (so ``answered`` is False). Surface that text
        # instead of a opaque "no response" error.
        if report:
            raise RuntimeError(f"PostHog report failed: {report}")
        raise RuntimeError(
            "PostHog report failed: the reasoning client did not produce a response."
        )
    if not report:
        raise RuntimeError(
            "PostHog report failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["build_report_prompt", "run_posthog_report"]
