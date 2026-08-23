# ======== from tools/victoria_logs_tool/ ========

"""VictoriaLogs structured-log query tool.

Queries the VictoriaLogs ``/select/logsql/query`` endpoint to surface
structured log evidence during root-cause investigations.

Executor contract: the investigation executor invokes tools as
``tool.run(**tool.extract_params(sources))``. Every credential and
parameter ``run()`` needs MUST be returned by ``extract_params()`` —
``run()`` does not receive a ``sources`` kwarg, so reading credentials
from ``kwargs["sources"]`` (as prior attempts did) makes the tool
permanently non-functional from the executor path. See
``AlertmanagerAlertsTool`` for the canonical pattern this tool follows.
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.domain.types.evidence import (
    CATALOG_ENTRIES_KEY,
    EvidenceMapper,
    record_evidence_entry,
)
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.victoria_logs.client import make_victoria_logs_client


def _map_victoria_logs_query(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Lift VictoriaLogs query rows into citeable report evidence.

    Each query invocation gets its own numbered catalog source
    (``victoria_logs_query#1``, ``#2``, ...) so repeated queries never collapse
    into one ambiguous citation and a claim can cite the exact query that
    supports it.
    """
    rows = output.get("rows", [])
    if not rows:
        return

    base_source = "victoria_logs_query"
    entries = evidence.get(CATALOG_ENTRIES_KEY, [])
    prior_count = 0
    if isinstance(entries, list):
        prior_count = sum(
            1
            for e in entries
            if isinstance(e, dict)
            and isinstance(e.get("source"), str)
            and e["source"].startswith(base_source)
        )
    occurrence = prior_count + 1
    source = f"{base_source}#{occurrence}"
    label = f"VictoriaLogs Query #{occurrence}"

    query = output.get("query")
    record_evidence_entry(
        evidence,
        source=source,
        label=label,
        summary=f"{len(rows)} log entries",
        snippet=str(query)[:200] if query else None,
    )
    # Expose the per-query key to the diagnosis/claim pipeline as well, not only
    # to the report catalog. This lets generated identifiers be cited directly.
    evidence[f"{base_source}#{occurrence}"] = {
        "query": query,
        "rows": rows,
        "total": len(rows),
    }


class VictoriaLogsTool(BaseTool):
    """Query VictoriaLogs via LogsQL to retrieve structured log evidence."""

    name = "victoria_logs_query"
    source = "victoria_logs"
    evidence_mapper: ClassVar[EvidenceMapper | None] = _map_victoria_logs_query
    description = (
        "Query structured logs from VictoriaLogs using LogsQL to investigate "
        "application errors, request anomalies, or other log-correlated signals."
    )
    use_cases = [
        "Investigating application logs for errors related to a firing alert",
        "Filtering structured log streams by service, level, or trace ID",
        "Correlating recent log volume changes with an incident timeline",
    ]
    # Expose the tool to both surfaces. The registry's default for class-based
    # tools without an explicit ``surfaces`` is investigation-only, which would
    # hide this from chat-mode investigations where log queries are a common
    # follow-up. Mirrors SplunkSearchTool, the closest log-query analog.
    surfaces = (ToolSurface.INVESTIGATION, ToolSurface.CHAT)
    requires = ["base_url"]
    injected_params = ["base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "VictoriaLogs base URL (e.g. http://vmlogs:9428)",
            },
            "tenant_id": {
                "type": "string",
                "default": "",
                "description": "Optional VictoriaLogs tenant ID; sent as the AccountID header.",
            },
            "query": {
                "type": "string",
                "default": "*",
                "description": (
                    "LogsQL query string (e.g. `_stream_id:* AND error`). Defaults to "
                    "the wildcard `*`; alert-derived query targeting through the "
                    "executor path is a known follow-up."
                ),
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of log entries to return.",
            },
            "start": {
                "type": "string",
                "default": "-1h",
                "description": "Time range expression accepted by VictoriaLogs (e.g. -1h, -24h).",
            },
        },
        "required": ["base_url"],
    }
    outputs = {
        "rows": "List of structured log entries returned by the LogsQL query.",
        "total": "Number of rows returned (after limit applied).",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("victoria_logs", {}).get("base_url"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        """Return every kwarg ``run()`` needs.

        The executor calls ``tool.run(**tool.extract_params(sources))`` with
        no ``sources`` kwarg, so connection config (``base_url``, ``tenant_id``)
        must be surfaced here alongside the LogsQL query parameters.
        """
        config = sources.get("victoria_logs", {})
        return {
            "base_url": config.get("base_url", ""),
            "tenant_id": config.get("tenant_id"),
            "query": config.get("query") or "*",
            "limit": config.get("limit", 50),
            "start": config.get("start", "-1h"),
        }

    def run(
        self,
        base_url: str,
        query: str = "*",
        tenant_id: str | None = None,
        limit: int = 50,
        start: str = "-1h",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_victoria_logs_client(base_url, tenant_id=tenant_id)
        if client is None:
            return tool_unavailable(
                "victoria_logs",
                "VictoriaLogs integration is not configured (missing base_url).",
                rows=[],
                total=0,
            )

        with client:
            result = client.query_logs(query=query, limit=limit, start=start)

        if not result.get("success"):
            return tool_unavailable(
                "victoria_logs", result.get("error", "unknown error"), rows=[], total=0
            )

        rows = result.get("rows", [])
        return {
            "source": "victoria_logs",
            "available": True,
            "rows": rows,
            "total": len(rows),
            "query": query,
            "limit": limit,
            "start": start,
        }


victoria_logs_query = VictoriaLogsTool()
