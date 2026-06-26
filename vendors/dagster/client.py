"""Dagster GraphQL client.

All methods return ``{"data": <parsed>}`` on success or ``{"error": "<message>"}``
on transport, HTTP, or GraphQL-level errors, so callers don't branch on exceptions.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from vendors.dagster.queries import (
    GET_RUN_LOGS,
    LIST_ASSETS,
    LIST_RUNS,
    LIST_SCHEDULE_TICKS,
    LIST_SENSOR_TICKS,
)

logger = logging.getLogger(__name__)

_API_TOKEN_HEADER = "Dagster-Cloud-Api-Token"


class DagsterClient:
    """GraphQL client targeting a Dagster instance.

    Auth token, when set, is sent as ``Dagster-Cloud-Api-Token`` (Cloud's
    canonical header, also accepted by OSS instances behind matching proxies).
    """

    def __init__(
        self,
        endpoint: str,
        api_token: str = "",
        timeout_s: int = 10,
        http_client: httpx.Client | None = None,
    ) -> None:
        # Normalise the endpoint so callers can paste any of:
        #   https://<host>/<deployment>
        #   https://<host>/<deployment>/
        #   https://<host>/<deployment>/graphql
        # All collapse to the canonical base; the client appends /graphql itself.
        self.endpoint = endpoint.rstrip("/").removesuffix("/graphql")
        self.api_token = api_token
        self.timeout_s = timeout_s
        self._graphql_url = f"{self.endpoint}/graphql"
        # When http_client is None we build a default httpx.Client with our
        # auth + content-type headers and timeout. Tests inject their own
        # client (typically backed by an httpx.MockTransport) to mock
        # network behaviour.
        self._client = http_client or httpx.Client(
            headers=self._default_headers(),
            timeout=self.timeout_s,
            follow_redirects=True,
        )

    def _default_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers[_API_TOKEN_HEADER] = self.api_token
        return headers

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DagsterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST a GraphQL query and return either ``{"data": ...}`` or ``{"error": ...}``."""
        try:
            response = self._client.post(
                self._graphql_url,
                json={"query": query, "variables": variables},
            )
        except httpx.RequestError as exc:
            return {"error": f"Request to Dagster failed: {exc}"}

        if response.status_code != 200:
            body_preview = response.text[:200] if response.text else ""
            return {"error": f"HTTP {response.status_code}: {body_preview}"}

        try:
            payload = response.json()
        except ValueError as exc:
            return {"error": f"Invalid JSON in Dagster response: {exc}"}

        if "errors" in payload:
            messages = [str(err.get("message", err)) for err in payload["errors"]]
            return {"error": "; ".join(messages)}

        data = payload.get("data") or {}
        return {"data": data}

    def ping(self) -> dict[str, Any]:
        """Lightweight version query to confirm the endpoint is a live Dagster."""
        return self._post("query DagsterPing { version }", {})

    def list_runs(
        self,
        *,
        limit: int = 25,
        status: str | None = None,
        job_name: str | None = None,
    ) -> dict[str, Any]:
        """Issue the ListRuns query.

        ``status`` is a single ``RunStatus`` (e.g. ``"FAILURE"``), sent as a
        single-element ``RunsFilter.statuses``. ``job_name`` is sent as
        ``RunsFilter.pipelineName`` (Dagster's back-compat alias for jobName).
        """
        variables: dict[str, Any] = {"limit": limit}
        if status:
            variables["statuses"] = [status]
        if job_name:
            variables["pipelineName"] = job_name
        return self._post(LIST_RUNS, variables)

    def get_run_logs(
        self, *, run_id: str, limit: int = 250, cursor: str | None = None
    ) -> dict[str, Any]:
        """Issue the GetRunLogs query for a specific run id (single page)."""
        variables: dict[str, Any] = {"runId": run_id, "limit": limit}
        if cursor is not None:
            variables["afterCursor"] = cursor
        return self._post(GET_RUN_LOGS, variables)

    def list_assets_with_materialization(self, *, limit: int = 25) -> dict[str, Any]:
        """Issue the ListAssets query and return the parsed payload."""
        return self._post(LIST_ASSETS, {"limit": limit})

    def list_sensor_ticks(
        self,
        *,
        repository_name: str,
        repository_location_name: str,
        sensor_name: str,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Issue the SensorTicks query for a fully-qualified sensor coordinate."""
        sensor_selector = {
            "repositoryName": repository_name,
            "repositoryLocationName": repository_location_name,
            "sensorName": sensor_name,
        }
        return self._post(
            LIST_SENSOR_TICKS,
            {"sensorSelector": sensor_selector, "limit": limit},
        )

    def list_schedule_ticks(
        self,
        *,
        repository_name: str,
        repository_location_name: str,
        schedule_name: str,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Issue the ScheduleTicks query for a fully-qualified schedule coordinate."""
        schedule_selector = {
            "repositoryName": repository_name,
            "repositoryLocationName": repository_location_name,
            "scheduleName": schedule_name,
        }
        return self._post(
            LIST_SCHEDULE_TICKS,
            {"scheduleSelector": schedule_selector, "limit": limit},
        )
