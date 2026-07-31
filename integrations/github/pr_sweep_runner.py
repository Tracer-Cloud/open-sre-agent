"""Headless GitHub PR sweep for scheduled Slack delivery."""

from __future__ import annotations

import logging

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from platform.harness_ports import configured_integration_services
from platform.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_PR_SWEEP_PROMPT = (
    "GitHub PR sweep for engineering standup: use summarize_github_pr_status and "
    "list_github_work_items (or the github-workflow skill) to report mergeable PRs, "
    "stale/superseded PRs, and conflicted PRs. Format a short Slack-ready plain-text "
    "digest with owners to ping. If GitHub is not configured, say so clearly."
)


def _require_github_configured() -> None:
    if "github" not in configured_integration_services():
        raise RuntimeError(
            "GitHub is not configured. Run `opensre integrations setup github` and verify "
            "with `opensre integrations verify github` before scheduling a PR sweep."
        )


def run_github_pr_sweep(payload: AgentPayload) -> str:
    """Run one headless turn that produces a PR sweep digest."""
    del payload  # reserved for future repo/org scoping
    _require_github_configured()

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
        message=_PR_SWEEP_PROMPT,
        gather_enabled=True,
        is_tty=False,
    )
    harness.attach_agent(agent)
    result = harness.dispatch_message(_PR_SWEEP_PROMPT)
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError(
            "GitHub PR sweep failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["run_github_pr_sweep"]
