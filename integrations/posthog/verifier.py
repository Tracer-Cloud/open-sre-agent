"""PostHog credential and connectivity verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

import integrations.posthog.client as client
from integrations._validation_helpers import report_validation_failure
from integrations.posthog.config import PostHogConfig, build_posthog_config
from integrations.verification import register_validation_verifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostHogValidationResult:
    ok: bool
    detail: str


def validate_posthog_config(config: PostHogConfig) -> PostHogValidationResult:
    if not config.project_id:
        return PostHogValidationResult(ok=False, detail="PostHog project ID is required.")
    if not config.personal_api_key:
        return PostHogValidationResult(ok=False, detail="PostHog API key is required.")

    try:
        client._request_json(
            config,
            "GET",
            f"/api/projects/{config.project_id}/",
        )
        return PostHogValidationResult(ok=True, detail="PostHog validated.")
    except httpx.HTTPStatusError as err:
        snippet = err.response.text[:200].strip()
        detail = (
            f"HTTP {err.response.status_code}: {snippet}"
            if snippet
            else f"HTTP {err.response.status_code}"
        )
        return PostHogValidationResult(ok=False, detail=detail)
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="posthog",
            method="validate_posthog_config",
        )
        return PostHogValidationResult(ok=False, detail=str(err))


verify_posthog = register_validation_verifier(
    "posthog",
    build_config=build_posthog_config,
    validate_config=validate_posthog_config,
)
