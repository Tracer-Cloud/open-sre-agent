"""Trello connection settings and config builders."""

import os
from typing import Any

from pydantic import BaseModel, Field, field_validator

DEFAULT_TRELLO_BASE_URL = "https://api.trello.com/1"


class TrelloConfig(BaseModel):
    """Normalized Trello connection settings."""

    base_url: str = DEFAULT_TRELLO_BASE_URL
    api_key: str = ""
    token: str = ""
    board_id: str = ""
    list_id: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_TRELLO_BASE_URL).strip()
        return normalized or DEFAULT_TRELLO_BASE_URL

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")


def build_trello_config(raw: dict[str, Any] | None) -> TrelloConfig:
    """Build a normalized Trello config object from env/store data."""
    return TrelloConfig.model_validate(raw or {})


def trello_config_from_env() -> TrelloConfig | None:
    """Load a Trello config from env vars."""
    api_key = os.getenv("TRELLO_API_KEY", "").strip()
    token = os.getenv("TRELLO_TOKEN", "").strip()
    if not api_key or not token:
        return None

    return build_trello_config(
        {
            "base_url": os.getenv("TRELLO_BASE_URL", DEFAULT_TRELLO_BASE_URL).strip()
            or DEFAULT_TRELLO_BASE_URL,
            "api_key": api_key,
            "token": token,
            "board_id": os.getenv("TRELLO_BOARD_ID", "").strip(),
            "list_id": os.getenv("TRELLO_LIST_ID", "").strip(),
        }
    )
