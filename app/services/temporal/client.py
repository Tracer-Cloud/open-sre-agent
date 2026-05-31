"""Temporal HTTP API client.

Uses Temporal's HTTP API (port 7233 REST gateway, or Temporal Cloud's API).
Falls back to the Temporal HTTP-API spec:
  POST /api/v1/namespaces/{namespace}/workflows  → list workflows
  GET  /api/v1/namespaces/{namespace}/workflows/{id}/runs/{runId}/history
  GET  /api/v1/namespaces/{namespace}/task-queues/{queue}
  GET  /api/v1/namespaces/{namespace}            → namespace metrics
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.temporal import TemporalConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_PAGE_SIZE = 50


class TemporalClientError(Exception):
    """Raised when a Temporal API call fails."""


class TemporalClient:
    """Thin HTTP client for the Temporal REST/HTTP-API gateway."""

    def __init__(self, config: TemporalConfig) -> None:
        self.config = config
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key:
            self._headers["Authorization"] = f"Bearer {config.api_key}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(url, headers=self._headers, params=params or {})
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            raise TemporalClientError(
                f"Temporal API {exc.response.status_code} for {url}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise TemporalClientError(
                f"Failed to connect to Temporal at {url}: {exc}"
            ) from exc

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.post(url, headers=self._headers, json=body or {})
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            raise TemporalClientError(
                f"Temporal API {exc.response.status_code} for {url}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise TemporalClientError(
                f"Failed to connect to Temporal at {url}: {exc}"
            ) from exc

    def list_workflows(
        self,
        query: str = "",
        page_size: int = MAX_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """List recent workflow executions.

        Args:
            query: Temporal visibility query e.g. ``ExecutionStatus='Failed'``.
            page_size: Max results (capped at 50).
        """
        ns = quote(self.config.namespace, safe="")
        body: dict[str, Any] = {"pageSize": min(page_size, MAX_PAGE_SIZE)}
        if query:
            body["query"] = query
        data = self._post(f"/api/v1/namespaces/{ns}/workflows", body)
        return list(data.get("executions", []))

    def get_workflow_history(
        self,
        workflow_id: str,
        run_id: str,
        max_event_count: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch event history for a specific workflow run."""
        ns = quote(self.config.namespace, safe="")
        wid = quote(workflow_id, safe="")
        rid = quote(run_id, safe="")
        path = f"/api/v1/namespaces/{ns}/workflows/{wid}/runs/{rid}/history"
        data = self._get(path, params={"maximumPageSize": max_event_count})
        return list(data.get("history", {}).get("events", []))

    def list_task_queues(self, task_queue: str) -> dict[str, Any]:
        """Fetch pollers and status for a task queue."""
        ns = quote(self.config.namespace, safe="")
        tq = quote(task_queue, safe="")
        return self._get(f"/api/v1/namespaces/{ns}/task-queues/{tq}")

    def get_namespace_metrics(self) -> dict[str, Any]:
        """Fetch namespace-level summary (open workflows, error counts)."""
        ns = quote(self.config.namespace, safe="")
        return self._get(f"/api/v1/namespaces/{ns}")

    def get_workflow_count(self) -> dict[str, Any]:
        """Fetch open workflow count for the namespace."""
        ns = quote(self.config.namespace, safe="")
        return self._get(f"/api/v1/namespaces/{ns}/workflows/count")
