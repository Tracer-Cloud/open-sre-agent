"""Internal helpers shared by relational integrations."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from pydantic import field_validator

from app.strict_config import StrictConfigModel

_TRUE_ENV_VALUES = frozenset({"true", "1", "yes"})


def env_bool(name: str, default: bool) -> bool:
    """Return a boolean environment variable with common truthy handling."""
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in _TRUE_ENV_VALUES


def env_int(name: str, default: int) -> int:
    """Return an integer environment variable, falling back on invalid input."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdecimal() else default


def env_str(name: str, default: str = "") -> str:
    """Return a stripped environment variable with an optional fallback."""
    normalized = os.getenv(name, default).strip()
    return normalized or default


class RelationalConfigBase(StrictConfigModel):
    """Shared field validators for relational DB config models (host, database, username)."""

    @field_validator("host", mode="before", check_fields=False)
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before", check_fields=False)
    @classmethod
    def _normalize_database(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before", check_fields=False)
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()


def resolve_stored_or_env_config[ConfigT](
    service: str,
    *,
    host: str,
    database: str,
    port: int,
    build_config: Callable[[dict[str, Any] | None], ConfigT],
    env_loader: Callable[[], ConfigT | None],
    extra_from_credentials: Callable[[dict[str, Any]], dict[str, Any]],
    extra_from_env: Callable[[ConfigT], dict[str, Any]],
) -> ConfigT:
    """Resolve a relational config from store first, then env, then identifiers only."""
    from app.integrations.store import get_integration

    stored = get_integration(service)
    if stored:
        credentials = stored.get("credentials", {})
        if isinstance(credentials, dict):
            return build_config(
                {
                    "host": host,
                    "port": credentials.get("port", port),
                    "database": database,
                    **extra_from_credentials(credentials),
                }
            )

    env_config = env_loader()
    if env_config is not None:
        return build_config(
            {
                "host": host,
                "port": port,
                "database": database,
                **extra_from_env(env_config),
            }
        )

    return build_config({"host": host, "port": port, "database": database})
