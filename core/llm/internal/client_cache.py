"""Process-wide cache of built LLM clients — one per role, invalidated together.

The whole cache clears when the ``(transport, provider)`` config key changes, so a
``/model`` switch or env change rebuilds every client against the new configuration.
Kept role-agnostic (``Hashable`` keys) so it does not depend on the factory's role
enum. Its companion, ``client_cache_key``, computes the invalidation key.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

ConfigKey = tuple[str, str]


class LLMClientCache:
    """One client per role; the whole cache clears when the config key changes."""

    def __init__(self) -> None:
        self._clients: dict[Hashable, Any] = {}
        self._config_key: ConfigKey | None = None

    def get(self, role: Hashable, config_key: ConfigKey | None) -> Any | None:
        """Return the cached client for *role*, clearing everything first if the config changed."""
        if self._config_key != config_key:
            self._clients.clear()
            self._config_key = config_key
        return self._clients.get(role)

    def store(self, role: Hashable, client: Any) -> None:
        self._clients[role] = client

    def clear(self) -> None:
        self._clients.clear()
        self._config_key = None
