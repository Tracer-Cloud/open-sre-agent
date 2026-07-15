"""Multiplex scheduled headless digests onto the shared agent_runner slot."""

from __future__ import annotations

from integrations.github.pr_sweep_runner import run_github_pr_sweep
from integrations.sentry.morning_digest_runner import run_sentry_morning_digest
from platform.scheduler.agent_runner import AgentPayload, register_agent_runner


def run_scheduled_agent_digest(payload: AgentPayload) -> str:
    """Route by ``payload['source']`` to Sentry digest or GitHub PR sweep."""
    source = str(payload.get("source") or "")
    if "github_pr" in source:
        return run_github_pr_sweep(payload)
    return run_sentry_morning_digest(payload)


def install() -> None:
    """Bind the multiplexed scheduled agent runner."""
    register_agent_runner(run_scheduled_agent_digest)


__all__ = ["install", "run_scheduled_agent_digest"]
