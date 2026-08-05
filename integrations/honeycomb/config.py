"""Honeycomb integration configuration."""

from __future__ import annotations

from pydantic import field_validator

from config.strict_config import StrictConfigModel
from integrations._validators import normalize_url, normalize_with_default

DEFAULT_HONEYCOMB_BASE_URL = "https://api.honeycomb.io"
DEFAULT_HONEYCOMB_DATASET = "__all__"


class HoneycombIntegrationConfig(StrictConfigModel):
    """Normalized Honeycomb credentials used by resolution and verification flows."""

    api_key: str
    dataset: str = DEFAULT_HONEYCOMB_DATASET
    base_url: str = DEFAULT_HONEYCOMB_BASE_URL
    integration_id: str = ""

    _normalize_dataset = field_validator("dataset", mode="before")(
        normalize_with_default(DEFAULT_HONEYCOMB_DATASET)
    )
    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url(DEFAULT_HONEYCOMB_BASE_URL)
    )
