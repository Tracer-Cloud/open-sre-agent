"""Unified Grafana Cloud client composed from mixins."""

from __future__ import annotations

import logging

from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig
from integrations.grafana.loki import LokiMixin
from integrations.grafana.mimir import MimirMixin
from integrations.grafana.tempo import TempoMixin

logger = logging.getLogger(__name__)

_GrafanaClientCacheKey = tuple[str, str, str, str, str, bool, str]
_grafana_client_cache: dict[_GrafanaClientCacheKey, GrafanaClient] = {}


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass


def get_grafana_client() -> GrafanaClient:
    """Create a Grafana client from environment variables."""
    import os

    return get_grafana_client_from_credentials(
        endpoint=os.getenv("GRAFANA_INSTANCE_URL", "https://tracerbio.grafana.net"),
        api_key=os.getenv("GRAFANA_READ_TOKEN", ""),
        account_id="env_default",
        verify_ssl=os.getenv("GRAFANA_VERIFY_SSL", "true"),
        ca_bundle=os.getenv("GRAFANA_CA_BUNDLE", ""),
    )


def get_grafana_client_from_credentials(
    endpoint: str,
    api_key: str,
    account_id: str = "user_integration",
    username: str = "",
    password: str = "",
    *,
    verify_ssl: bool | str = True,
    ca_bundle: str = "",
) -> GrafanaClient:
    """Create a Grafana client from integration credentials."""
    config = GrafanaAccountConfig.model_validate(
        {
            "account_id": account_id,
            "instance_url": endpoint,
            "read_token": api_key,
            "username": username,
            "password": password,
            "verify_ssl": verify_ssl,
            "ca_bundle": ca_bundle,
        }
    )
    cache_key: _GrafanaClientCacheKey = (
        config.account_id,
        config.instance_url,
        config.read_token,
        config.username,
        config.password,
        config.verify_ssl,
        config.ca_bundle,
    )
    if cache_key in _grafana_client_cache:
        return _grafana_client_cache[cache_key]

    client = GrafanaClient(config=config)

    discovered = client.discover_datasource_uids()
    if discovered:
        config = GrafanaAccountConfig(
            account_id=account_id,
            instance_url=endpoint.rstrip("/"),
            read_token=api_key,
            username=username,
            password=password,
            loki_datasource_uid=discovered.get("loki_uid", ""),
            tempo_datasource_uid=discovered.get("tempo_uid", ""),
            mimir_datasource_uid=discovered.get("mimir_uid", ""),
            verify_ssl=config.verify_ssl,
            ca_bundle=config.ca_bundle,
        )
        client = GrafanaClient(config=config)
        logger.info(
            "[grafana] Client ready for account_id=%s with datasource discovery status: loki=%s tempo=%s mimir=%s",
            account_id,
            config.loki_datasource_uid,
            config.tempo_datasource_uid,
            config.mimir_datasource_uid,
        )
    else:
        logger.warning(
            "[grafana] Could not discover datasource UIDs for account_id=%s — queries will fail",
            account_id,
        )

    _grafana_client_cache[cache_key] = client
    return client
