"""Unified Grafana Cloud client composed from mixins."""

from __future__ import annotations

import logging
import threading

from config.constants.grafana import (
    GRAFANA_CA_BUNDLE_ENV,
    GRAFANA_INSTANCE_URL_ENV,
    GRAFANA_READ_TOKEN_ENV,
    GRAFANA_VERIFY_SSL_ENV,
)
from config.grafana_cloud import DEFAULT_INSTANCE_URL, get_datasource_uids
from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig
from integrations.grafana.loki import LokiMixin
from integrations.grafana.mimir import MimirMixin
from integrations.grafana.tempo import TempoMixin

logger = logging.getLogger(__name__)

_grafana_client_cache: dict[str, GrafanaClient] = {}
#: Datasource discovery is one network round trip per account. An investigation
#: queries Mimir, Loki, Tempo and alert rules concurrently, so without this the
#: first callers all miss the empty cache and each runs its own discovery — and
#: against an unreachable Grafana each one waits out the full connect timeout.
_grafana_client_lock = threading.Lock()


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass


def get_grafana_client() -> GrafanaClient:
    """Create a Grafana client from environment variables."""
    import os

    from config.llm_credentials import resolve_env_credential

    return get_grafana_client_from_credentials(
        endpoint=os.getenv(GRAFANA_INSTANCE_URL_ENV, DEFAULT_INSTANCE_URL),
        api_key=resolve_env_credential(GRAFANA_READ_TOKEN_ENV),
        account_id="env_default",
        verify_ssl=os.getenv(GRAFANA_VERIFY_SSL_ENV, "true").strip().lower() != "false",
        ca_bundle=os.getenv(GRAFANA_CA_BUNDLE_ENV, "").strip(),
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
    cache_key = f"creds_{account_id}_{endpoint}"
    cached = _grafana_client_cache.get(cache_key)
    if cached is not None:
        return cached
    with _grafana_client_lock:
        # Re-check inside the lock: a caller that queued behind the discovery
        # above must reuse its result rather than repeat the round trip.
        cached = _grafana_client_cache.get(cache_key)
        if cached is not None:
            return cached
        return _build_and_cache_client(
            cache_key=cache_key,
            endpoint=endpoint,
            api_key=api_key,
            account_id=account_id,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )


def _build_and_cache_client(
    *,
    cache_key: str,
    endpoint: str,
    api_key: str,
    account_id: str,
    username: str,
    password: str,
    verify_ssl: bool,
    ca_bundle: str,
) -> GrafanaClient:
    """Discover datasources once and cache the resulting client.

    Prefer live discovery; when a UID is missing, fall back to
    ``GRAFANA_*_DATASOURCE_UID`` / Grafana Cloud defaults so env-configured
    installs still work when auto-discovery is incomplete.
    """
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

    discovered = client.discover_datasource_uids() or {}
    fallback_loki, fallback_tempo, fallback_mimir = get_datasource_uids()
    loki_uid = discovered.get("loki_uid") or fallback_loki
    tempo_uid = discovered.get("tempo_uid") or fallback_tempo
    mimir_uid = discovered.get("mimir_uid") or fallback_mimir

    if loki_uid or tempo_uid or mimir_uid:
        config = GrafanaAccountConfig(
            account_id=account_id,
            instance_url=endpoint.rstrip("/"),
            read_token=api_key,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
            loki_datasource_uid=loki_uid,
            tempo_datasource_uid=tempo_uid,
            mimir_datasource_uid=mimir_uid,
        )
        client = GrafanaClient(config=config)
        logger.info(
            "[grafana] Client ready for account_id=%s with datasource UIDs: "
            "loki=%s tempo=%s mimir=%s (discovered=%s)",
            account_id,
            config.loki_datasource_uid,
            config.tempo_datasource_uid,
            config.mimir_datasource_uid,
            bool(discovered),
        )
    else:
        logger.warning(
            "[grafana] Could not resolve datasource UIDs for account_id=%s — queries will fail",
            account_id,
        )

    _grafana_client_cache[cache_key] = client
    return client
