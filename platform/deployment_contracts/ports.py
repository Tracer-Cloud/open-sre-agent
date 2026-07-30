"""Gateway-facing repository protocols for multi-tenant agent runs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from platform.deployment_contracts.models import AgentRun, AgentRunSource, AgentRunStatus


class AgentRunRepository(Protocol):
    """Durable store for agent runs, one queue per organization."""

    def enqueue_agent_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
    ) -> AgentRun:
        """Queue a new run for ``organization_id`` and return it."""

    def fetch_agent_run_by_id(self, run_id: str) -> AgentRun | None:
        """The run with ``run_id``, or None when no such run exists."""

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        """Lease the oldest queued run to ``worker_id``, or None when the queue is empty."""

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        """Renew the lease. False when ``worker_id`` no longer owns the run."""

    def finalize_owned_agent_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Record the terminal ``status``. False when ``worker_id`` lost ownership."""
