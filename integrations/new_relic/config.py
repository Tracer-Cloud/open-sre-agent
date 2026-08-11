"""New Relic integration configuration."""

from __future__ import annotations

from pydantic import field_validator

from config.strict_config import StrictConfigModel
from integrations._validators import normalize_str, normalize_url

#: ``base_url`` only moves for EU tenants (``https://api.eu.newrelic.com``) —
#: same shape as Honeycomb's ``base_url``.
DEFAULT_NEW_RELIC_BASE_URL = "https://api.newrelic.com"


class NewRelicIntegrationConfig(StrictConfigModel):
    """Normalized New Relic credentials used by resolution and verification flows."""

    api_key: str
    account_id: str = ""
    base_url: str = DEFAULT_NEW_RELIC_BASE_URL
    integration_id: str = ""

    _normalize_account_id = field_validator("account_id", mode="before")(normalize_str())
    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url(DEFAULT_NEW_RELIC_BASE_URL)
    )
