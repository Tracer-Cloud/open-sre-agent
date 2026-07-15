"""PostHog connection settings and config builders."""

from __future__ import annotations

import os
from typing import Any

from pydantic import Field, field_validator

from config.constants.posthog import (
    DEFAULT_POSTHOG_TIMEOUT_SECONDS,
    DEFAULT_POSTHOG_URL,
)
from config.strict_config import StrictConfigModel


class PostHogConfig(StrictConfigModel):
    """Normalized PostHog connection settings."""

    base_url: str = DEFAULT_POSTHOG_URL
    project_id: str = ""
    personal_api_key: str = ""
    timeout_seconds: float = Field(default=DEFAULT_POSTHOG_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_POSTHOG_URL).strip()
        return normalized or DEFAULT_POSTHOG_URL

    @field_validator("project_id", mode="before")
    @classmethod
    def _normalize_project_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("personal_api_key", mode="before")
    @classmethod
    def _normalize_personal_api_key(cls, value: Any) -> str:
        return str(value or "").strip()

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.personal_api_key}",
            "Accept": "application/json",
        }


def build_posthog_config(raw: dict[str, Any] | None) -> PostHogConfig:
    return PostHogConfig.model_validate(raw or {})


def posthog_config_from_env() -> PostHogConfig | None:
    project_id = os.getenv("POSTHOG_PROJECT_ID", "").strip()
    personal_api_key = os.getenv("POSTHOG_PERSONAL_API_KEY", "").strip()

    if not project_id or not personal_api_key:
        return None

    return build_posthog_config(
        {
            "base_url": os.getenv("POSTHOG_BASE_URL", DEFAULT_POSTHOG_URL),
            "project_id": project_id,
            "personal_api_key": personal_api_key,
            "timeout_seconds": os.getenv(
                "POSTHOG_TIMEOUT_SECONDS", str(DEFAULT_POSTHOG_TIMEOUT_SECONDS)
            ),
        }
    )
