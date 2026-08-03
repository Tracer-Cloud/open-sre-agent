"""Gateway-facing repository protocols for multi-tenant agent runs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from platform.deployment_contracts.models import AgentRun, AgentRunStatus


class AgentRunRepository(Protocol):
    """What a gateway worker needs from the agent-run queue.

    Only the three calls a worker makes: take a run, hold it, finish it. Writing
    runs into the queue is the caller's side of the API and is not modelled here.
    """

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
