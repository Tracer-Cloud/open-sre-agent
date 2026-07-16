"""Headless GitHub PR sweep for scheduled Slack delivery."""

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
        accounting=DefaultTurnAccounting(session, _PR_SWEEP_PROMPT),
        error_reporter=error_reporter,
        gather_enabled=True,
        is_tty=False,
    )
    result = agent.dispatch(_PR_SWEEP_PROMPT)
    report = (result.assistant_response_text or result.action_result.response_text).strip()
    if not result.answered or not report:
        raise RuntimeError(
            "GitHub PR sweep failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["run_github_pr_sweep"]
