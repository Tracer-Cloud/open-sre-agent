"""Yandex Cloud integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig

logger = logging.getLogger(__name__)

SERVICE = "yandex_cloud"


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[YandexCloudIntegrationConfig | None, str | None]:
    raw: dict[str, Any] = {
        "folder_id": credentials.get("folder_id", ""),
        "cloud_id": credentials.get("cloud_id", ""),
        "sa_key_file": credentials.get("sa_key_file", ""),
        "sa_key": credentials.get("sa_key", ""),
        "oauth_token": credentials.get("oauth_token", ""),
        "iam_token": credentials.get("iam_token", ""),
        "use_metadata": credentials.get("use_metadata", False),
        "integration_id": record_id,
    }
    try:
        return YandexCloudIntegrationConfig.model_validate(raw), SERVICE
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration=SERVICE, record_id=record_id)
        return None, None


__all__ = ["SERVICE", "classify"]
