"""Temporal integration classification helpers.

The Temporal connection config itself lives in
``app.integrations.config_models.TemporalIntegrationConfig`` (re-exported as
``TemporalConfig`` from ``app.services.temporal``). This module provides the
``classify`` entry point the catalog uses to turn a stored/remote integration
record into a flat, typed config the Temporal tools can consume.
"""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.services.temporal import TemporalConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[TemporalConfig | None, str | None]:
    """Build a typed TemporalConfig from a raw integration record.

    Returns ``(config, "temporal")`` when the record carries the minimum
    connection fields (base_url + namespace), else ``(None, None)`` so the
    catalog skips the instance. Mirrors the ``classify`` contract used by the
    other integrations (e.g. tempo, signoz).
    """
    try:
        cfg = TemporalConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "api_key": credentials.get("api_key", ""),
                "namespace": credentials.get("namespace", "default"),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="temporal", record_id=record_id)
        return None, None
    if cfg.base_url and cfg.namespace:
        return cfg, "temporal"
    return None, None
