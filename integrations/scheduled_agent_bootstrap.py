"""Multiplex scheduled headless digests onto the shared agent_runner slot."""

from __future__ import annotations

from integrations.github.pr_sweep_runner import run_github_pr_sweep
from integrations.sentry.morning_digest_runner import run_sentry_morning_digest
from integrations.sentry.uptime import run_uptime_watch_tick
from platform.scheduler.agent_runner import AgentPayload, register_agent_runner


def run_scheduled_agent_digest(payload: AgentPayload) -> str:
    """Route by ``payload['source']`` to Sentry digest, uptime watch, or GitHub PR sweep."""
    source = str(payload.get("source") or "")
    if "uptime_watch" in source:
        return run_uptime_watch_tick(
            task_id=str(payload.get("task_id") or "cli"),
            project_slug=str(payload.get("project_slug") or "").strip(),
        )
    if "github_pr" in source:
        return run_github_pr_sweep(payload)
    return run_sentry_morning_digest(payload)


def install() -> None:
    """Bind the multiplexed scheduled agent runner."""
    register_agent_runner(run_scheduled_agent_digest)


__all__ = ["install", "run_scheduled_agent_digest"]
