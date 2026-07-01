"""Pre-load Hugging Face CSV telemetry into investigation evidence.

Uses the same stack as ``integrations/opensre/`` CSV Grafana backend: ``OpenSRECsvGrafanaBackend``
plus ``query_grafana_*`` tool functions so evidence matches normal tool output shapes.
"""

from __future__ import annotations

from typing import Any

from core.domain.pipeline_spans import extract_pipeline_spans
from integrations.opensre.csv_grafana_backend import OpenSRECsvGrafanaBackend
from integrations.opensre.grafana_backend_queries import (
    query_logs_from_backend,
    query_metrics_from_backend,
    query_traces_from_backend,
)
from integrations.opensre.grafana_mappers import (
    _map_grafana_logs,
    _map_grafana_metrics,
    _map_grafana_traces,
)
from integrations.opensre.inject import (
    inject_opensre_into_resolved_integrations,
    resolve_opensre_telemetry_dir,
)


def merge_opensre_seed_into_state(
    raw_alert: dict[str, Any],
    resolved_integrations: dict[str, Any] | None,
    existing_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a partial state dict: ``resolved_integrations`` and merged ``evidence``."""
    merged = inject_opensre_into_resolved_integrations(raw_alert, resolved_integrations)
    if merged is None:
        merged = dict(resolved_integrations or {})

    telemetry_dir = resolve_opensre_telemetry_dir(raw_alert)
    evidence = dict(existing_evidence or {})

    if telemetry_dir is None:
        return {"resolved_integrations": merged, "evidence": evidence}

    backend = OpenSRECsvGrafanaBackend(telemetry_dir=telemetry_dir, alert_fixture=raw_alert)

    evidence.update(
        {
            "opensre_telemetry_dir": str(telemetry_dir),
            "opensre_telemetry_seed": True,
        }
    )
    evidence.update(
        _map_grafana_metrics(
            query_metrics_from_backend(
                backend,
                metric_name="",
                service_name=None,
            )
        )
    )
    evidence.update(
        _map_grafana_logs(
            query_logs_from_backend(
                backend,
                service_name="",
                execution_run_id=None,
            )
        )
    )
    evidence.update(
        _map_grafana_traces(
            query_traces_from_backend(
                backend,
                service_name="",
                execution_run_id=None,
                limit=50,
                # Without this, ``pipeline_spans`` is always ``[]`` and
                # ``_map_grafana_traces`` strips ``grafana_pipeline_spans``
                # from seeded evidence — regression flagged by Greptile.
                extract_pipeline_spans=extract_pipeline_spans,
            )
        )
    )

    return {"resolved_integrations": merged, "evidence": evidence}
