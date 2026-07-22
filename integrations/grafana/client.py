"""Unified Grafana Cloud client composed from mixins."""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig
from integrations.grafana.loki import LokiMixin
from integrations.grafana.mimir import MimirMixin
from integrations.grafana.tempo import TempoMixin

logger = logging.getLogger(__name__)

_MAX_GRAFANA_CLIENT_CACHE_SIZE = 64


@dataclass(frozen=True, slots=True)
class _GrafanaClientCacheKey:
    """Connection identity without retaining raw credentials in the key."""

    account_id: str
    instance_url: str
    credentials_fingerprint: str
    verify_ssl: bool
    ca_bundle: str


_grafana_client_cache: OrderedDict[_GrafanaClientCacheKey, GrafanaClient] = OrderedDict()
_grafana_client_cache_lock = Lock()


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass


def _credentials_fingerprint(config: GrafanaAccountConfig) -> str:
    """Hash length-prefixed auth fields so cache keys never contain secrets."""
    digest = hashlib.sha256()
    for value in (config.read_token, config.username, config.password):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _client_cache_key(config: GrafanaAccountConfig) -> _GrafanaClientCacheKey:
    return _GrafanaClientCacheKey(
        account_id=config.account_id,
        instance_url=config.instance_url,
        credentials_fingerprint=_credentials_fingerprint(config),
        verify_ssl=config.verify_ssl,
        ca_bundle=config.ca_bundle,
    )


def _get_cached_client(cache_key: _GrafanaClientCacheKey) -> GrafanaClient | None:
    with _grafana_client_cache_lock:
        client = _grafana_client_cache.get(cache_key)
        if client is not None:
            _grafana_client_cache.move_to_end(cache_key)
        return client


def _cache_client(cache_key: _GrafanaClientCacheKey, client: GrafanaClient) -> GrafanaClient:
    """Cache a client, preferring one concurrently created for the same config."""
    with _grafana_client_cache_lock:
        existing = _grafana_client_cache.get(cache_key)
        if existing is not None:
            _grafana_client_cache.move_to_end(cache_key)
            return existing

        _grafana_client_cache[cache_key] = client
        _grafana_client_cache.move_to_end(cache_key)
        while len(_grafana_client_cache) > _MAX_GRAFANA_CLIENT_CACHE_SIZE:
            _grafana_client_cache.popitem(last=False)
        return client


def clear_grafana_client_cache() -> None:
    """Clear cached clients after integration lifecycle changes or in tests."""
    with _grafana_client_cache_lock:
        _grafana_client_cache.clear()


def get_grafana_client() -> GrafanaClient:
    """Create a Grafana client from environment variables."""
    import os

    from config.llm_credentials import resolve_env_credential

    return get_grafana_client_from_credentials(
        endpoint=os.getenv("GRAFANA_INSTANCE_URL", "https://tracerbio.grafana.net"),
        api_key=resolve_env_credential("GRAFANA_READ_TOKEN"),
        account_id="env_default",
        verify_ssl=os.getenv("GRAFANA_VERIFY_SSL", "true").strip().lower() != "false",
        ca_bundle=os.getenv("GRAFANA_CA_BUNDLE", "").strip(),
    )


def get_grafana_client_from_credentials(
    endpoint: str,
    api_key: str,
    account_id: str = "user_integration",
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    ca_bundle: str = "",
) -> GrafanaClient:
    """Create a Grafana client from integration credentials."""
    config = GrafanaAccountConfig(
        account_id=account_id,
        instance_url=endpoint,
        read_token=api_key,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        ca_bundle=ca_bundle,
    )
    cache_key = _client_cache_key(config)
    cached_client = _get_cached_client(cache_key)
    if cached_client is not None:
        return cached_client

    client = GrafanaClient(config=config)

    discovered = client.discover_datasource_uids()
    if discovered:
        config = GrafanaAccountConfig(
            account_id=account_id,
            instance_url=endpoint,
            read_token=api_key,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
            loki_datasource_uid=discovered.get("loki_uid", ""),
            tempo_datasource_uid=discovered.get("tempo_uid", ""),
            mimir_datasource_uid=discovered.get("mimir_uid", ""),
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

    return _cache_client(cache_key, client)
