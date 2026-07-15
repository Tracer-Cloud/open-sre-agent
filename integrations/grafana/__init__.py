"""Grafana integration classifier.

Splits a validated :class:`GrafanaIntegrationConfig` into one of two resolved
keys so the rest of the stack can treat local and cloud Grafana separately:

* ``grafana`` — a remote/cloud instance authenticated with a service account
  token.
* ``grafana_local`` — a locally hosted instance (localhost / loopback), whether
  it authenticates with a token, basic auth, or anonymously.

Authentication details (token vs basic vs anonymous) are owned by
``GrafanaIntegrationConfig``; this module only decides local vs cloud routing.
"""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import GrafanaIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[GrafanaIntegrationConfig | None, str | None]:
    try:
        cfg = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": credentials.get("endpoint", ""),
                "api_key": credentials.get("api_key", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="grafana", record_id=record_id)
        return None, None
    if not cfg.endpoint:
        return None, None
    if cfg.is_local:
        if cfg.is_anonymous_local:
            # Drop the "local" sentinel so downstream auth stays anonymous.
            return cfg.model_copy(update={"api_key": ""}), "grafana_local"
        return cfg, "grafana_local"
    if cfg.has_token:
        return cfg, "grafana"
    return None, None
