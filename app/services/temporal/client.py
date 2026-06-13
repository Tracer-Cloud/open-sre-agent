from __future__ import annotations

import logging
from typing import Any

import httpx

from app.integrations.config_models import TemporalIntegrationConfig
from app.services._error_helpers import capture_service_error

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_DEFAULT_PAGE_SIZE = 10
TemporalConfig = TemporalIntegrationConfig


class TemporalClient:
    def __init__(self, config: TemporalConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=self.config.base_url,
            headers=self.config.headers,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.config.base_url) and bool(self.config.namespace)

    def list_workflow_executions(self, next_page_token: str | None = None) -> dict[str, Any]:
        """List recent workflow executions with status and failure reason.

        Returns paginated workflow executions for the configured namespace.
        Each execution includes workflowId, type, status, taskQueue, and timing info.
        """
        params = {
            "pageSize": _DEFAULT_PAGE_SIZE,
        }
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token

        try:
            r = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}/workflows", params=params
            )
            r.raise_for_status()
            data = r.json()

            return {
                "success": True,
                "executions": data["executions"],
                "next_page_token": data["nextPageToken"],
                "total": len(data["executions"]),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="list_workflow_executions",
                extras={"query": params},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="list_workflow_executions",
                extras={"query": params},
            )
            return {"success": False, "error": str(exc)}

    def get_workflow_history(
        self, workflow_id: str, run_id: str | None = None, next_page_token: str | None = None
    ) -> dict[str, Any]:
        """Fetch the event history for a specific workflow execution.

        Returns the ordered sequence of events (started, activity scheduled,
        activity failed, workflow failed, etc.) that tells the story of what
        happened during the execution. Essential for diagnosing why a workflow failed.
        """
        params = {
            "pageSize": _DEFAULT_PAGE_SIZE,
        }
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token
        if run_id is not None:
            params["execution.runId"] = run_id
        try:
            r = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}/workflows/{workflow_id}/history",
                params=params,
            )
            r.raise_for_status()
            data = r.json()
            history = data["history"]
            return {
                "success": True,
                "events": history["events"],
                "next_page_token": data["nextPageToken"],
                "archived": data["archived"],
                "total": len(history["events"]),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_workflow_history",
                extras={"query": params},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_workflow_history",
                extras={"query": params},
            )
            return {"success": False, "error": str(exc)}

    def describe_task_queue(self, task_queue_name: str) -> dict[str, Any]:
        """Describe a task queue's pollers and backlog stats.

        The Temporal HTTP API does not expose a "list all task queues" endpoint.
        Instead, task queue names are discovered from workflow executions (each
        execution reports which task queue it ran on). This method describes a
        single queue by name — returning active pollers (workers) and backlog
        metrics (approximate count, age, add/dispatch rates).

        Use the taskQueue field from list_workflow_executions() results to
        identify which queues to inspect.
        """
        params = {"reportStats": True, "taskQueueType": "TASK_QUEUE_TYPE_WORKFLOW"}
        try:
            r = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}/task-queues/{task_queue_name}",
                params=params,
            )
            r.raise_for_status()
            data = r.json()

            return {
                "success": True,
                "pollers": data["pollers"],
                "stats": data.get("stats", {}),
                "total": len(data["pollers"]),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="describe_task_queue",
                extras={"query": params},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="describe_task_queue",
                extras={"query": params},
            )
            return {"success": False, "error": str(exc)}

    def get_namespace_info(self) -> dict[str, Any]:
        """Fetch namespace state and workflow counts grouped by execution status.

        Combines DescribeNamespace (state, config) with CountWorkflowExecutions
        (grouped by ExecutionStatus) to provide namespace-level health metrics.
        """
        try:
            ns_resp = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}",
            )
            ns_resp.raise_for_status()
            ns_data = ns_resp.json()

            count_resp = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}/workflow-count",
                params={"query": "GROUP BY ExecutionStatus"},
            )
            count_resp.raise_for_status()
            count_data = count_resp.json()

            namespace_info = ns_data.get("namespaceInfo", {})
            return {
                "success": True,
                "name": namespace_info.get("name", ""),
                "state": namespace_info.get("state", ""),
                "workflow_count": count_data.get("count", "0"),
                "groups": count_data.get("groups", []),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_namespace_info",
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_namespace_info",
            )
            return {"success": False, "error": str(exc)}

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> TemporalClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
