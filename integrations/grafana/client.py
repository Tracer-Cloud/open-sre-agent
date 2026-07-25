"""Unified Grafana Cloud client composed from mixins."""

from __future__ import annotations

import hashlib
import logging

from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig
from integrations.grafana.loki import LokiMixin
from integrations.grafana.mimir import MimirMixin
from integrations.grafana.tempo import TempoMixin

logger = logging.getLogger(__name__)

_grafana_client_cache: dict[str, GrafanaClient] = {}


def _credential_fingerprint(
    *,
    api_key: str,
    username: str,
    password: str,
    verify_ssl: bool,
    ca_bundle: str,
) -> str:
    """Hash everything that changes how requests authenticate or verify TLS.

    Used as part of the client cache key so rotating a token, switching auth
    mode, or changing TLS trust invalidates the cached client instead of
    silently reusing one built from the previous credentials (#4192). Hashed
    rather than stored raw so the cache key never carries a live secret in
    memory/logs.
    """
    fingerprint_input = "\x1f".join(
        (api_key, username, password, str(verify_ssl), ca_bundle)
    ).encode("utf-8")
    return hashlib.sha256(fingerprint_input).hexdigest()


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass


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
    fingerprint = _credential_fingerprint(
        api_key=api_key,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        ca_bundle=ca_bundle,
    )
    cache_key_prefix = f"creds_{account_id}_{endpoint}_"
    cache_key = f"{cache_key_prefix}{fingerprint}"
    if cache_key in _grafana_client_cache:
        return _grafana_client_cache[cache_key]

    # Drop any client cached under the old credentials for this account_id +
    # endpoint — it is superseded and would otherwise linger indefinitely.
    for stale_key in [
        key
        for key in _grafana_client_cache
        if key.startswith(cache_key_prefix) and key != cache_key
    ]:
        del _grafana_client_cache[stale_key]

    config = GrafanaAccountConfig(
        account_id=account_id,
        instance_url=endpoint.rstrip("/"),
        read_token=api_key,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        ca_bundle=ca_bundle,
    )
    client = GrafanaClient(config=config)

    discovered = client.discover_datasource_uids()
    if discovered:
        config = GrafanaAccountConfig(
            account_id=account_id,
            instance_url=endpoint.rstrip("/"),
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

    _grafana_client_cache[cache_key] = client
    return client


def clear_grafana_client_cache() -> None:
    """Drop every cached client; test-only, real processes never need this."""
    _grafana_client_cache.clear()
