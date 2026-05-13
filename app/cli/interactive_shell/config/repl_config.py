"""REPL configuration — three-tier resolution: file → env var → CLI flag."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

_VALID_LAYOUTS = ("classic", "pinned")
_FALSE_VALUES = ("", "0", "false", "off", "no")

log = logging.getLogger(__name__)

# ── Release notes ─────────────────────────────────────────────────────────────
# Shown in the "What's new" panel on startup. Update this each release with
# exactly 2 user-visible changes. Keep each entry under ~50 chars so it fits
# the right column without truncation. The banner reads this at import time.

WHATS_NEW: tuple[str, ...] = (
    "Confidence scoring now shown during diagnosis",
    "New /save command exports investigation reports",
)


def _read_config_file() -> dict[str, Any]:
    """Read the interactive section from ~/.config/opensre/config.yml.

    Returns an empty dict if the file is missing, unreadable, or malformed.
    Failures are always silent — a bad config file must never crash the CLI.
    """
    try:
        import yaml  # type: ignore[import-untyped]

        from app.constants import OPENSRE_HOME_DIR

        config_path = OPENSRE_HOME_DIR / "config.yml"
        if not config_path.exists():
            return {}

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}

        interactive = data.get("interactive", {})
        if not isinstance(interactive, dict):
            return {}

        return interactive
    except Exception:
        return {}


@dataclass(frozen=True)
class ReplConfig:
    """Two-axis REPL configuration.

    Axes
    ----
    enabled : bool
        When False the REPL is skipped and ``opensre`` falls back to
        ``render_landing()``.  Controlled by ``--no-interactive`` CLI flag,
        the ``OPENSRE_INTERACTIVE`` env var, or ``interactive.enabled`` in
        ``~/.config/opensre/config.yml``.

    layout : str  ("classic" | "pinned")
        Which renderer to use.  Only ``classic`` is wired today; ``pinned``
        is accepted and stored so the flag round-trips cleanly once P3 lands.
        Controlled by ``--layout`` CLI option, ``OPENSRE_LAYOUT`` env var, or
        ``interactive.layout`` in ``~/.config/opensre/config.yml``.

    reload : bool
        When True, the interactive shell watches repo-local Python files and
        reloads changed modules between prompt turns. Controlled by the
        ``--reload`` / ``--no-reload`` CLI option, ``OPENSRE_RELOAD`` env var,
        or ``interactive.reload`` in ``~/.config/opensre/config.yml``.
    """

    enabled: bool = True
    layout: str = "classic"
    reload: bool = True
    alert_listener_enabled: bool = False
    alert_listener_host: str = "127.0.0.1"
    alert_listener_port: int = 0
    alert_listener_token: str | None = None

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() not in _FALSE_VALUES

    @classmethod
    def load(
        cls,
        *,
        cli_enabled: bool | None = None,
        cli_layout: str | None = None,
        cli_reload: bool | None = None,
    ) -> ReplConfig:
        """Resolve config from all three tiers.

        Priority (highest wins):
            1. CLI flag   — ``cli_enabled`` / ``cli_layout`` / ``cli_reload`` params
            2. Env var    — ``OPENSRE_INTERACTIVE`` / ``OPENSRE_LAYOUT`` / ``OPENSRE_RELOAD``
            3. Config file — ``~/.config/opensre/config.yml`` ``interactive`` section
            4. Built-in defaults (enabled=True, layout="classic")
        """
        file_conf = _read_config_file()

        # --- enabled ---
        if cli_enabled is not None:
            enabled = cli_enabled
        elif (env_val := os.getenv("OPENSRE_INTERACTIVE")) is not None:
            enabled = cls._coerce_bool(env_val, default=True)
        else:
            enabled = cls._coerce_bool(file_conf.get("enabled"), default=True)

        # --- layout ---
        if cli_layout is not None:
            layout = cli_layout.lower()
        elif (env_val := os.getenv("OPENSRE_LAYOUT")) is not None:
            layout = env_val.lower()
        else:
            layout = str(file_conf.get("layout", "classic")).lower()

        if layout not in _VALID_LAYOUTS:
            layout = "classic"

        # --- reload ---
        if cli_reload is not None:
            reload = cli_reload
        elif (env_val := os.getenv("OPENSRE_RELOAD")) is not None:
            reload = cls._coerce_bool(env_val, default=True)
        else:
            reload = cls._coerce_bool(file_conf.get("reload"), default=True)

        # --- alert_listener_enabled ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_ENABLED")) is not None:
            alert_listener_enabled = cls._coerce_bool(env_val, default=False)
        else:
            alert_listener_enabled = cls._coerce_bool(
                file_conf.get("alert_listener_enabled"), default=False
            )

        # --- alert_listener_host ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_HOST")) is not None:
            alert_listener_host = env_val.strip()
        else:
            alert_listener_host = str(file_conf.get("alert_listener_host", "127.0.0.1"))

        # --- alert_listener_port ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_PORT")) is not None:
            try:
                alert_listener_port = int(env_val.strip())
            except ValueError:
                log.warning(
                    "OPENSRE_ALERT_LISTENER_PORT=%r is not a valid port number; defaulting to 0 (random).",
                    env_val,
                )
                alert_listener_port = 0
        else:
            try:
                alert_listener_port = int(file_conf.get("alert_listener_port", 0))
            except ValueError:
                log.warning(
                    "config.yml interactive.alert_listener_port=%r is not a valid port number; defaulting to 0 (random).",
                    file_conf.get("alert_listener_port"),
                )
                alert_listener_port = 0

        # --- alert_listener_token ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_TOKEN")) is not None:
            alert_listener_token = env_val.strip() or None
        else:
            alert_listener_token = file_conf.get("alert_listener_token") or None

        return cls(
            enabled=enabled,
            layout=layout,
            reload=reload,
            alert_listener_enabled=alert_listener_enabled,
            alert_listener_host=alert_listener_host,
            alert_listener_port=alert_listener_port,
            alert_listener_token=alert_listener_token,
        )

    @classmethod
    def from_env(cls) -> ReplConfig:
        """Convenience alias — loads from env + file, no CLI override."""
        return cls.load()


def read_history_settings() -> dict[str, Any]:
    """Return the ``interactive.history`` config block, or empty dict."""
    interactive = _read_config_file()
    raw = interactive.get("history", {})
    return raw if isinstance(raw, dict) else {}
