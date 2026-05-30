"""Mimir metrics query mixin for Grafana Cloud client."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from app.services._error_helpers import capture_service_error

if TYPE_CHECKING:
    from app.services.grafana.base import GrafanaClientBase

logger = logging.getLogger(__name__)


class MimirMixin:
    """Mixin providing Mimir metrics query capabilities."""

    def query_mimir(  # type: ignore[misc]
        self: GrafanaClientBase,
        metric_name: str,
        service_name: str | None = None,
    ) -> dict[str, Any]:
        """Query Grafana Cloud Mimir for metrics.

        Args:
            metric_name: Prometheus metric name (e.g., pipeline_runs_total)
            service_name: Optional service name filter

        Returns:
            Dictionary with metric series and values
        """
        if not self.is_configured:
            return {
                "success": False,
                "error": f"Grafana client not configured for account '{self.account_id}'",
                "metrics": [],
            }

        url = self._build_datasource_url(
            self.mimir_datasource_uid,
            "/api/v1/query",
        )

        query = metric_name
        if service_name:
            query = f'{metric_name}{{service_name="{service_name}"}}'

        params = {"query": query}

        try:
            data = self._make_request(url, params=params)
            result = data.get("data", {}).get("result", [])

            metrics = []
            for series in result:
                metrics.append(
                    {
                        "metric": series.get("metric", {}),
                        "value": series.get("value", []),
                    }
                )

            return {
                "success": True,
                "metrics": metrics,
                "total_series": len(result),
                "query": query,
                "account_id": self.account_id,
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc, logger=logger, integration="grafana", method="query_mimir"
            )
            return {
                "success": False,
                "error": f"Mimir query failed: {exc.response.status_code}",
                "response": exc.response.text[:300],
                "metrics": [],
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="grafana", method="query_mimir"
            )
            return {"success": False, "error": str(exc), "metrics": []}
