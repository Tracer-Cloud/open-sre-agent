"""PostHog REST integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.posthog.config import PostHogConfig, build_posthog_config

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[PostHogConfig | None, str | None]:
    raw: dict[str, Any] = {
        "base_url": credentials.get("base_url", ""),
        "project_id": credentials.get("project_id", ""),
        "personal_api_key": credentials.get("personal_api_key", ""),
        "integration_id": record_id,
    }
    if credentials.get("timeout_seconds") is not None:
        raw["timeout_seconds"] = credentials["timeout_seconds"]
    try:
        cfg = build_posthog_config(raw)
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="posthog", record_id=record_id)
        return None, None
    if cfg.project_id and cfg.personal_api_key:
        return cfg, "posthog"
    return None, None
