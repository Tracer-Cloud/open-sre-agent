"""Register the Sentry morning digest as the scheduler's agent runner."""

from __future__ import annotations

from integrations.sentry.morning_digest_runner import run_sentry_morning_digest
from platform.scheduler.agent_runner import register_agent_runner


def install() -> None:
    """Bind the Sentry morning digest runner for scheduled delivery tasks."""
    register_agent_runner(run_sentry_morning_digest)


__all__ = ["install"]
