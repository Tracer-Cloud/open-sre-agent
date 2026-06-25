"""Grafana integration verifier — datasource discovery probe."""

from __future__ import annotations

from typing import Any

import requests

from app.integrations.config_models import GrafanaIntegrationConfig
from app.integrations.verification import register_verifier, result

_SUPPORTED_GRAFANA_TYPES = ("loki", "tempo", "prometheus")


@register_verifier("grafana")
def verify_grafana(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        grafana_config = GrafanaIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("grafana", source, "missing", str(err))
    endpoint = grafana_config.endpoint
    api_key = grafana_config.api_key
    if not endpoint or not api_key:
        return result("grafana", source, "missing", "Missing endpoint or API token.")

    try:
        response = requests.get(
            f"{endpoint}/api/datasources",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return result("grafana", source, "failed", f"Datasource discovery failed: {exc}")

    datasources = payload if isinstance(payload, list) else []
    supported_types = sorted(
        {
            datasource_type
            for datasource in datasources
            for datasource_type in [str(datasource.get("type", "")).lower()]
            if any(keyword in datasource_type for keyword in _SUPPORTED_GRAFANA_TYPES)
        }
    )
    if not supported_types:
        return result(
            "grafana",
            source,
            "failed",
            "Connected, but no Loki, Tempo, or Prometheus datasources were discovered.",
        )

    return result(
        "grafana",
        source,
        "passed",
        f"Connected to {endpoint} and discovered {', '.join(supported_types)} datasources.",
    )
