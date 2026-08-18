"""Availability and parameter extraction shared by the Yandex Cloud family.

Only one integration record is registered for Yandex Cloud, and every sibling
package — logging, monitoring, compute and the rest — reads its credentials
from that single ``yandex_cloud`` source entry. The helpers here are how they
do it, mirroring how the AWS sub-service tools share the ``aws`` record.

Synthetic tests inject a fixture through the ``_backend`` slot on the source
dict, so availability accepts either real credentials or a backend.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Final

from integrations.yandex_cloud.config import YandexCloudIntegrationConfig
from integrations.yandex_cloud.rest_client import YandexCloudClient

SOURCE = "yandex_cloud"


def yc_source(sources: dict[str, dict]) -> dict[str, Any]:
    """Return the Yandex Cloud source entry, or an empty dict when absent."""
    return sources.get(SOURCE, {}) or {}


def yc_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when the catalog resolved a usable config, or a backend is attached.

    Credentials come from the integration catalog like every other vendor's:
    the store record (or the ``YC_*`` environment) is classified once and lands
    in ``sources``. Tools never read the environment themselves.

    Synthetic tests attach a fixture through the ``_backend`` slot instead, so
    they exercise the tool bodies without credentials or network.
    """
    source = yc_source(sources)
    if source.get("_backend") is not None:
        return True
    return config_from_params(source) is not None


def yc_credentials(sources: dict[str, dict]) -> dict[str, Any]:
    """Return the credential fields tools hand to the client.

    Copied out of ``sources`` rather than referenced, so a tool that adds the
    synthetic backend below cannot mutate the catalog's own entry.
    """
    source = yc_source(sources)
    creds = {key: source[key] for key in YC_INJECTED_PARAMS if key in source}
    backend = source.get("_backend")
    if backend is not None:
        creds["yc_backend"] = backend
    return creds


#: Credential keys tools declare as injected, so they never appear in the
#: schema the model sees.
YC_INJECTED_PARAMS: tuple[str, ...] = (
    "folder_id",
    "cloud_id",
    "sa_key_file",
    "sa_key",
    "oauth_token",
    "iam_token",
    "use_metadata",
    "yc_backend",
)


def config_from_params(params: Mapping[str, Any]) -> YandexCloudIntegrationConfig | None:
    """Build a config from the credentials injected into a tool call.

    Returns None when they do not validate — which for a tool means reporting
    itself unavailable rather than raising, since a missing credential is a
    setup problem the agent cannot solve mid-investigation.
    """
    try:
        return YandexCloudIntegrationConfig.model_validate(
            {
                "folder_id": params.get("folder_id", ""),
                "cloud_id": params.get("cloud_id", ""),
                "sa_key_file": params.get("sa_key_file", ""),
                "sa_key": params.get("sa_key", ""),
                "oauth_token": params.get("oauth_token", ""),
                "iam_token": params.get("iam_token", ""),
                "use_metadata": params.get("use_metadata", False),
            }
        )
    except Exception:
        return None


#: Clients kept per credential set. Small on purpose: an investigation uses one
#: credential set, and the extra slots only cover a token rotating mid-run.
_CLIENT_CACHE_SIZE: Final = 8

_client_cache: OrderedDict[str, YandexCloudClient] = OrderedDict()
_client_cache_lock = threading.Lock()


def _credential_cache_key(config: YandexCloudIntegrationConfig) -> str:
    """A non-secret cache slot for the client bound to this credential set.

    Built from non-sensitive fields only — folder, cloud, auth mode — so no
    secret becomes a dictionary key a debugger or crash dump could surface, and
    none is fed to a hash function (CodeQL ``py/weak-sensitive-data-hashing``).

    It is a slot, not an identity: two credential sets for the same account
    share it, and a rotated secret keeps it. ``client_from_params`` therefore
    confirms the cached client's config still matches before reusing it, rather
    than trusting the key alone.
    """
    return "\x00".join((config.folder_id, config.cloud_id, config.auth_mode or ""))


def client_from_params(params: Mapping[str, Any]) -> YandexCloudClient | None:
    """Return a REST client for the injected credentials, or None when unusable.

    Clients are reused across tool calls. A client owns the IAM token cache, and
    every tool builds its client from the credentials injected into that one
    call, so a fresh instance per call meant the cache never lived long enough
    to be read: an investigation minted a token — and on a VM, made a metadata
    round-trip for it — once per tool call rather than once per run.

    A cached client is reused only when its config still equals the requested
    one. The key is non-secret, so a rotated token or key lands on the same
    slot; without the equality check a long-running gateway would keep handing
    back a client holding the superseded credential.
    """
    config = config_from_params(params)
    if config is None:
        return None

    key = _credential_cache_key(config)
    with _client_cache_lock:
        cached = _client_cache.get(key)
        if cached is not None and cached.config == config:
            _client_cache.move_to_end(key)
            return cached

        client = YandexCloudClient(config)
        _client_cache[key] = client
        _client_cache.move_to_end(key)
        if len(_client_cache) > _CLIENT_CACHE_SIZE:
            _client_cache.popitem(last=False)
        return client


def reset_client_cache() -> None:
    """Drop every cached client, so a test starts from a cold token cache."""
    with _client_cache_lock:
        _client_cache.clear()


__all__ = [
    "SOURCE",
    "YC_INJECTED_PARAMS",
    "client_from_params",
    "config_from_params",
    "reset_client_cache",
    "yc_available_or_backend",
    "yc_credentials",
    "yc_source",
]
