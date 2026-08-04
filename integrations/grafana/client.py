"""Unified Grafana Cloud client composed from mixins."""

from __future__ import annotations

import hashlib
import logging
import threading

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

#: Length of the hex slice of the credential/TLS fingerprint folded into the
#: cache key. 16 hex chars = 64 bits of SHA-256 — collision-safe for the handful
#: of live (account, endpoint) configs while keeping the key short. The raw
#: secrets are never placed in the key (they can leak into logs/reprs); only
#: this hash is.
_FINGERPRINT_HEX_LEN = 16
_CACHE_KEY_PREFIX = "creds"
#: Separator between the ``(prefix, account, endpoint)`` identity of a cache key
#: and its trailing credential/TLS fingerprint. Sharing this identity prefix but
#: differing in the fingerprint marks a superseded credential version to evict.
_FINGERPRINT_SEP = "#"


def _cache_key_prefix(*, account_id: str, endpoint: str) -> str:
    """Identity prefix shared by every credential version of one (account, endpoint).

    The endpoint is normalized the same way ``GrafanaAccountConfig`` normalizes
    it (``strip().rstrip("/")``) so trivially-different-but-equivalent inputs map
    to the same identity.
    """
    normalized_endpoint = endpoint.strip().rstrip("/")
    return f"{_CACHE_KEY_PREFIX}_{account_id}_{normalized_endpoint}"


def _cache_key(
    *,
    endpoint: str,
    api_key: str,
    account_id: str,
    username: str,
    password: str,
    verify_ssl: bool,
    ca_bundle: str,
) -> str:
    """Build a cache key that changes when any auth or TLS input changes.

    Every string component is normalized before hashing (``strip()`` on
    credentials/CA bundle, ``strip().rstrip("/")`` on the endpoint) so inputs
    that differ only by surrounding whitespace share one cache entry instead of
    triggering duplicate datasource discovery. The sensitive tuple is hashed —
    never embedded in plaintext — so a rotated token or changed TLS config
    yields a fresh key without leaking the secret into the key string.
    """
    fingerprint = hashlib.sha256(
        repr(
            (api_key.strip(), username.strip(), password.strip(), verify_ssl, ca_bundle.strip())
        ).encode()
    ).hexdigest()[:_FINGERPRINT_HEX_LEN]
    return (
        f"{_cache_key_prefix(account_id=account_id, endpoint=endpoint)}"
        f"{_FINGERPRINT_SEP}{fingerprint}"
    )


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
    cache_key = _cache_key(
        endpoint=endpoint,
        api_key=api_key,
        account_id=account_id,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        ca_bundle=ca_bundle,
    )
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
    """Discover datasources once and cache the resulting client."""
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

    # Evict superseded credential versions of the same (account, endpoint): on
    # rotation a fresh fingerprint would otherwise leave the old token/password
    # cached forever. Runs under _grafana_client_lock (held by the sole caller).
    identity = f"{_cache_key_prefix(account_id=account_id, endpoint=endpoint)}{_FINGERPRINT_SEP}"
    for stale_key in [
        key for key in _grafana_client_cache if key != cache_key and key.startswith(identity)
    ]:
        del _grafana_client_cache[stale_key]

    _grafana_client_cache[cache_key] = client
    return client
