"""Rootly integration classifier and env-var config resolution."""

from __future__ import annotations

import logging
import os
from typing import Any

from config.constants.rootly import (
    ROOTLY_API_TOKEN_ENV,
    ROOTLY_BASE_URL_ENV,
    ROOTLY_TIMEOUT_SECONDS_ENV,
)
from config.llm_credentials import resolve_env_credential
from integrations._validation_helpers import report_classify_failure
from integrations.config_models import RootlyIntegrationConfig

logger = logging.getLogger(__name__)

RootlyConfig = RootlyIntegrationConfig


def rootly_config_from_env() -> RootlyConfig | None:
    """Build a Rootly config from the environment, or ``None`` when unset.

    The token is keyring-eligible so it goes through ``resolve_env_credential``;
    the URL and timeout are not secrets and read straight from the environment.

    A malformed value raises rather than returning ``None``: the caller reports
    the failure, and a silently dropped integration is indistinguishable from an
    unconfigured one.
    """
    api_token = resolve_env_credential(ROOTLY_API_TOKEN_ENV)
    if not api_token:
        return None
    return RootlyConfig.model_validate(
        {
            "api_token": api_token,
            "base_url": os.getenv(ROOTLY_BASE_URL_ENV, "").strip(),
            "timeout_seconds": os.getenv(ROOTLY_TIMEOUT_SECONDS_ENV, "").strip(),
        }
    )


def classify(credentials: dict[str, Any], record_id: str) -> tuple[RootlyConfig | None, str | None]:
    """Normalize a stored Rootly record into a config, or drop it."""
    try:
        cfg = RootlyConfig.model_validate(
            {
                "api_token": credentials.get("api_token", ""),
                "base_url": credentials.get("base_url", ""),
                "timeout_seconds": credentials.get("timeout_seconds", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="rootly", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "rootly"
    return None, None


__all__ = [
    "RootlyConfig",
    "classify",
    "rootly_config_from_env",
]
