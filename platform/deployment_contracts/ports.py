"""Gateway-facing repository protocols for multi-tenant agent runs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from platform.deployment_contracts.models import AgentRun, AgentRunSource, AgentRunStatus


class AgentRunRepository(Protocol):
    def enqueue_agent_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
    ) -> AgentRun: ...

    def fetch_agent_run_by_id(self, run_id: str) -> AgentRun | None: ...

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None: ...

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool: ...

    def finalize_owned_agent_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool: ...
