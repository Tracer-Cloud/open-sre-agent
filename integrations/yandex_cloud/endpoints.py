"""Yandex Cloud service-to-host resolution.

Yandex publishes a live registry of every service endpoint. Hosts differ per
region — the Kazakhstan installation exposes roughly half the services under
``.yandexcloud.kz`` — and services are added over time, so the registry is the
source of truth rather than a hardcoded list.

The checked-in snapshot below is what makes that safe: it is what resolution
falls back to when the registry is unreachable, which keeps tests and airgapped
runs working and means a network blip cannot take the integration down.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Final

import httpx

from config.constants.yandex_cloud import YC_API_ENDPOINT_ENV, YC_ENDPOINT_OVERRIDES_ENV

logger = logging.getLogger(__name__)

#: Default registry host. Override for another region, e.g. ``api.yandexcloud.kz``.
DEFAULT_REGISTRY_HOST: Final = "api.cloud.yandex.net"

_REGISTRY_TIMEOUT_SECONDS: Final = 5.0
_REGISTRY_CACHE_TTL_SECONDS: Final = 24 * 60 * 60

#: Snapshot of ``GET https://api.cloud.yandex.net/endpoints``. Regenerate with
#: ``make refresh-yc-endpoints``; ports are dropped because REST is always 443.
STATIC_ENDPOINTS: Final[dict[str, str]] = {
    "ai-assistants": "assistant.api.cloud.yandex.net",
    "ai-files": "assistant.api.cloud.yandex.net",
    "ai-foundation-models": "llm.api.cloud.yandex.net",
    "ai-llm": "llm.api.cloud.yandex.net",
    "ai-speechkit": "transcribe.api.cloud.yandex.net",
    "ai-stt": "transcribe.api.cloud.yandex.net",
    "ai-stt-v3": "stt.api.cloud.yandex.net",
    "ai-translate": "translate.api.cloud.yandex.net",
    "ai-vision": "vision.api.cloud.yandex.net",
    "ai-vision-ocr": "ocr.api.cloud.yandex.net",
    "alb": "alb.api.cloud.yandex.net",
    "alert-sink": "alert-sink.api.cloud.yandex.net",
    "apigateway-connections": "apigateway-connections.api.cloud.yandex.net",
    "application-load-balancer": "alb.api.cloud.yandex.net",
    "apploadbalancer": "alb.api.cloud.yandex.net",
    "audittrails": "audittrails.api.cloud.yandex.net",
    "baas": "backup.api.cloud.yandex.net",
    "backup": "backup.api.cloud.yandex.net",
    "baremetal": "baremetal.api.cloud.yandex.net",
    "billing": "billing.api.cloud.yandex.net",
    "broker-data": "iot-data.api.cloud.yandex.net",
    "cdn": "cdn.api.cloud.yandex.net",
    "certificate-manager": "certificate-manager.api.cloud.yandex.net",
    "certificate-manager-data": "data.certificate-manager.api.cloud.yandex.net",
    "certificate-manager-private-ca": "private-ca.certificate-manager.api.cloud.yandex.net",
    "certificate-manager-private-ca-data": "data.private-ca.certificate-manager.api.cloud.yandex.net",
    "cic": "cic.api.cloud.yandex.net",
    "cloud-registry": "registry.api.cloud.yandex.net",
    "cloudapps": "cloudapps.api.cloud.yandex.net",
    "cloudbackup": "backup.api.cloud.yandex.net",
    "clouddesktops": "clouddesktops.api.cloud.yandex.net",
    "cloudrouter": "cloudrouter.api.cloud.yandex.net",
    "cloudvideo": "video.api.cloud.yandex.net",
    "compute": "compute.api.cloud.yandex.net",
    "connection-manager": "connectionmanager.api.cloud.yandex.net",
    "container-registry": "container-registry.api.cloud.yandex.net",
    "datacatalog": "datacatalog.api.cloud.yandex.net",
    "dataproc": "dataproc.api.cloud.yandex.net",
    "dataproc-manager": "dataproc-manager.api.cloud.yandex.net",
    "datasphere": "datasphere.api.cloud.yandex.net",
    "datatransfer": "datatransfer.api.cloud.yandex.net",
    "dns": "dns.api.cloud.yandex.net",
    "dspm": "dspm.api.cloud.yandex.net",
    "endpoint": "api.cloud.yandex.net",
    "fomo-dataset": "fomo-dataset.api.cloud.yandex.net",
    "fomo-tuning": "fomo-tuning.api.cloud.yandex.net",
    "gitlab": "gitlab.api.cloud.yandex.net",
    "iam": "iam.api.cloud.yandex.net",
    "iot-broker": "iot-broker.api.cloud.yandex.net",
    "iot-data": "iot-data.api.cloud.yandex.net",
    "iot-devices": "iot-devices.api.cloud.yandex.net",
    "k8s": "mks.api.cloud.yandex.net",
    "kms": "kms.api.cloud.yandex.net",
    "kms-crypto": "kms.yandex",
    "kspm": "kspm.api.cloud.yandex.net",
    "load-balancer": "load-balancer.api.cloud.yandex.net",
    "locator": "locator.api.cloud.yandex.net",
    "lockbox": "lockbox.api.cloud.yandex.net",
    "lockbox-payload": "payload.lockbox.api.cloud.yandex.net",
    "log-ingestion": "ingester.logging.yandexcloud.net",
    "log-reading": "reader.logging.yandexcloud.net",
    "logging": "logging.api.cloud.yandex.net",
    "managed-airflow": "airflow.api.cloud.yandex.net",
    "managed-clickhouse": "mdb.api.cloud.yandex.net",
    "managed-elasticsearch": "mdb.api.cloud.yandex.net",
    "managed-greenplum": "mdb.api.cloud.yandex.net",
    "managed-kafka": "mdb.api.cloud.yandex.net",
    "managed-kubernetes": "mks.api.cloud.yandex.net",
    "managed-metastore": "metastore.api.cloud.yandex.net",
    "managed-mongodb": "mdb.api.cloud.yandex.net",
    "managed-mysql": "mdb.api.cloud.yandex.net",
    "managed-opensearch": "mdb.api.cloud.yandex.net",
    "managed-postgresql": "mdb.api.cloud.yandex.net",
    "managed-redis": "mdb.api.cloud.yandex.net",
    "managed-spark": "spark.api.cloud.yandex.net",
    "managed-spqr": "mdb.api.cloud.yandex.net",
    "managed-trino": "trino.api.cloud.yandex.net",
    "managed-ytsaurus": "ytsaurus.api.cloud.yandex.net",
    "marketplace": "marketplace.api.cloud.yandex.net",
    "marketplace-pim": "marketplace.api.cloud.yandex.net",
    "marketplace-stacklandlicenseapi": "marketplace.api.cloud.yandex.net",
    "mdb-clickhouse": "mdb.api.cloud.yandex.net",
    "mdb-mongodb": "mdb.api.cloud.yandex.net",
    "mdb-mysql": "mdb.api.cloud.yandex.net",
    "mdb-opensearch": "mdb.api.cloud.yandex.net",
    "mdb-postgresql": "mdb.api.cloud.yandex.net",
    "mdb-redis": "mdb.api.cloud.yandex.net",
    "mdb-spqr": "mdb.api.cloud.yandex.net",
    "mdbproxy": "mdbproxy.api.cloud.yandex.net",
    "monitoring": "monitoring.api.cloud.yandex.net",
    "operation": "operation.api.cloud.yandex.net",
    "organization-manager": "organization-manager.api.cloud.yandex.net",
    "organizationmanager": "organization-manager.api.cloud.yandex.net",
    "quota-manager": "quota-manager.api.cloud.yandex.net",
    "quotamanager": "quota-manager.api.cloud.yandex.net",
    "resource-manager": "resource-manager.api.cloud.yandex.net",
    "resourcemanager": "resource-manager.api.cloud.yandex.net",
    "searchapi": "searchapi.api.cloud.yandex.net",
    "serialssh": "serialssh.cloud.yandex.net",
    "serverless-apigateway": "serverless-apigateway.api.cloud.yandex.net",
    "serverless-containers": "serverless-containers.api.cloud.yandex.net",
    "serverless-eventrouter": "serverless-eventrouter.api.cloud.yandex.net",
    "serverless-functions": "serverless-functions.api.cloud.yandex.net",
    "serverless-gateway-connections": "apigateway-connections.api.cloud.yandex.net",
    "serverless-mcp-gateway": "serverless-mcp-gateway.api.cloud.yandex.net",
    "serverless-triggers": "serverless-triggers.api.cloud.yandex.net",
    "serverless-workflows": "serverless-workflows.api.cloud.yandex.net",
    "serverlesseventrouter-events": "events.eventrouter.serverless.yandexcloud.net",
    "smart-captcha": "smartcaptcha.api.cloud.yandex.net",
    "smart-web-security": "smartwebsecurity.api.cloud.yandex.net",
    "storage": "storage.yandexcloud.net",
    "storage-api": "storage.api.cloud.yandex.net",
    "video": "video.api.cloud.yandex.net",
    "vpc": "vpc.api.cloud.yandex.net",
    "ycvc": "yc-tools-version-control.cloud.yandex.net",
    "ydb": "ydb.api.cloud.yandex.net",
}


@dataclass
class _RegistryCache:
    """The last live registry response and when it was fetched.

    A single mutable holder rather than two module scalars reassigned through
    ``global``: the fields are only ever mutated, never rebound, so nothing here
    reads as an unused global.
    """

    endpoints: dict[str, str] | None = None
    #: When the registry was last *attempted*, or None if never. A sentinel
    #: rather than 0.0 so "never fetched" cannot be mistaken for "fetched long
    #: ago": ``time.monotonic()`` is small on a freshly booted host, so a
    #: zero baseline would read as still-fresh and skip the first real fetch.
    fetched_at: float | None = None


_cache_lock = threading.Lock()
_cache = _RegistryCache()


def _registry_url() -> str:
    host = os.getenv(YC_API_ENDPOINT_ENV, "").strip() or DEFAULT_REGISTRY_HOST
    host = host.split("://", maxsplit=1)[-1].strip("/")
    return f"https://{host}/endpoints"


def _fetch_endpoints() -> dict[str, str]:
    """Return the live registry as service id to host, or empty on any failure."""
    try:
        response = httpx.get(_registry_url(), timeout=_REGISTRY_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Yandex Cloud endpoint registry unavailable, using snapshot: %s", exc)
        return {}
    endpoints: dict[str, str] = {}
    for entry in payload.get("endpoints", []):
        service = str(entry.get("id", "")).strip()
        address = str(entry.get("address", "")).strip()
        if service and address:
            endpoints[service] = address.rsplit(":", maxsplit=1)[0]
    return endpoints


def _endpoint_overrides() -> dict[str, str]:
    """Return per-service host overrides from the environment."""
    raw = os.getenv(YC_ENDPOINT_OVERRIDES_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring malformed %s: %s", YC_ENDPOINT_OVERRIDES_ENV, exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring %s: expected a JSON object", YC_ENDPOINT_OVERRIDES_ENV)
        return {}
    return {str(key): str(value) for key, value in parsed.items() if key and value}


def known_endpoints(*, refresh: bool = True) -> dict[str, str]:
    """Return every known service host, newest registry data winning.

    The registry is fetched at most once per day per process; the snapshot fills
    in any service the live response omits, so resolution never regresses when a
    region serves a shorter list.
    """
    endpoints = dict(STATIC_ENDPOINTS)
    if refresh:
        with _cache_lock:
            # Never attempted yet, or the last attempt aged out. A failed fetch
            # still stamps the time, so the next attempt is a cache period away
            # rather than on the very next call.
            due = (
                _cache.fetched_at is None
                or time.monotonic() - _cache.fetched_at > _REGISTRY_CACHE_TTL_SECONDS
            )
            if due:
                fetched = _fetch_endpoints()
                if fetched:
                    _cache.endpoints = fetched
                # Stamp the attempt whether or not it succeeded: a failed fetch
                # falls back to the snapshot, and without this every later call
                # would retry the network and pay the timeout again instead of
                # backing off for the cache period.
                _cache.fetched_at = time.monotonic()
            cached = _cache.endpoints
        if cached:
            endpoints.update(cached)
    overrides = _endpoint_overrides()
    endpoints.update(overrides)
    _apply_host_aliases(endpoints, overrides)
    return endpoints


#: Services the registry names differently from everyone else. The index and the
#: tools call the Object Storage control plane "storage"; the registry gives that
#: name to the S3 data plane, which serves objects and answers no control-plane
#: read, and registers the control plane as "storage-api".
_HOST_ALIASES: Final[dict[str, str]] = {"storage": "storage-api"}


def _apply_host_aliases(endpoints: dict[str, str], overrides: dict[str, str]) -> None:
    """Re-point each aliased name at the host that answers for it.

    Applied to the resolved registry data rather than the snapshot, because the
    snapshot is refreshed from Yandex at runtime and the upstream value would
    come straight back.

    Either name is a fair thing for an operator to override, so both work: an
    override on the alias itself wins outright, and one on the registry name is
    what the alias then points at.
    """
    for name, registered_as in _HOST_ALIASES.items():
        if name in overrides:
            continue
        host = endpoints.get(registered_as)
        if host:
            endpoints[name] = host


def resolve_endpoint(service: str, *, refresh: bool = True) -> str | None:
    """Return the API host for *service*, or None when it is not a known service.

    Returning None rather than guessing a host is deliberate: the host is never
    built from caller input, so a model cannot talk the client into reaching an
    arbitrary address.
    """
    return known_endpoints(refresh=refresh).get(service.strip().lower())


def reset_endpoint_cache() -> None:
    """Drop the cached registry response, back to the never-attempted state."""
    with _cache_lock:
        _cache.endpoints = None
        _cache.fetched_at = None


__all__ = [
    "DEFAULT_REGISTRY_HOST",
    "STATIC_ENDPOINTS",
    "known_endpoints",
    "reset_endpoint_cache",
    "resolve_endpoint",
]
