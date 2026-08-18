"""The instance metadata service, for an agent running inside Yandex Cloud.

A VM or function with a service account attached can authenticate without any
credential being stored anywhere: the metadata service on the link-local address
issues IAM tokens for the attached account. It also knows which folder and cloud
the instance lives in, so a deployment inside Yandex Cloud needs no
configuration at all beyond saying that this is where it runs.

Everything here fails soft. Off a Yandex Cloud instance the address does not
answer, and the short timeout means asking costs nothing.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Final, NamedTuple

import httpx

logger = logging.getLogger(__name__)

BASE_URL: Final = "http://169.254.169.254/computeMetadata/v1"
HEADERS: Final = {"Metadata-Flavor": "Google"}

#: Short on purpose: off a Yandex Cloud instance this address is unroutable, and
#: nothing should wait on discovering that.
TIMEOUT_SECONDS: Final = 3.0

_TOKEN_PATH: Final = "/instance/service-accounts/default/token"
_FOLDER_ID_PATH: Final = "/instance/vendor/folder-id"
_CLOUD_ID_PATH: Final = "/instance/vendor/cloud-id"
_INSTANCE_ID_PATH: Final = "/instance/id"

#: Renew this long before the reported expiry, so a long investigation never
#: carries a token that ages out mid-run.
_EXPIRY_SAFETY_SECONDS: Final = 300
#: Used when the service reports no expiry, which it normally does report.
_FALLBACK_TTL_SECONDS: Final = 50 * 60


class MetadataToken(NamedTuple):
    """An IAM token from the metadata service, and how long it is good for."""

    token: str
    ttl_seconds: float


def _fetch(path: str) -> str | None:
    """Return the metadata value at *path*, or None when it is unavailable."""
    try:
        response = httpx.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Instance metadata unavailable at %s: %s", path, exc)
        return None
    return response.text.strip() or None


def fetch_token() -> MetadataToken | None:
    """Return an IAM token for the attached service account, or None."""
    try:
        response = httpx.get(f"{BASE_URL}{_TOKEN_PATH}", headers=HEADERS, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("Instance metadata token unavailable: %s", exc)
        return None

    token = str(payload.get("access_token", "")).strip()
    if not token:
        return None

    # The fallback covers a missing or unparseable expires_in, not a small one.
    # Folding them together (``... or _FALLBACK``) let a token with seconds left
    # be cached for the full fallback window, so a near-expired token kept being
    # handed out and cloud reads failed authorization.
    raw_expires_in = payload.get("expires_in")
    ttl: float
    if raw_expires_in is None:
        ttl = _FALLBACK_TTL_SECONDS
    else:
        try:
            expires_in = float(raw_expires_in)
        except (TypeError, ValueError):
            ttl = _FALLBACK_TTL_SECONDS
        else:
            ttl = max(expires_in - _EXPIRY_SAFETY_SECONDS, 0.0)
    return MetadataToken(token=token, ttl_seconds=ttl)


def fetch_folder_id() -> str | None:
    """Return the folder this instance runs in, or None when not on one."""
    return _fetch(_FOLDER_ID_PATH)


def fetch_cloud_id() -> str | None:
    """Return the cloud this instance runs in, or None when not on one."""
    return _fetch(_CLOUD_ID_PATH)


def fetch_instance_id() -> str | None:
    """Return this instance's own id, or None when not on one."""
    return _fetch(_INSTANCE_ID_PATH)


@lru_cache(maxsize=1)
def is_available() -> bool:
    """Return whether the metadata service answers, i.e. we run inside the cloud.

    Cached for the life of the process: a program does not move in or out of
    Yandex Cloud while it runs. Without the cache each call costs the full
    timeout wherever the answer is no, and the answer is consulted on every
    startup and on each pass through the setup wizard.
    """
    return fetch_folder_id() is not None


def forget_availability() -> None:
    """Drop the cached answer, so a test can pretend to be somewhere else."""
    is_available.cache_clear()


__all__ = [
    "BASE_URL",
    "HEADERS",
    "TIMEOUT_SECONDS",
    "MetadataToken",
    "fetch_cloud_id",
    "fetch_folder_id",
    "fetch_instance_id",
    "fetch_token",
    "forget_availability",
    "is_available",
]
