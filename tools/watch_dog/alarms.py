"""Telegram alarm dispatcher for the watchdog."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from platform.notifications.telegram_delivery import post_telegram_message, truncate_for_telegram_html
from platform.common.truncation import truncate
from cli.interactive_shell.error_handling.errors import OpenSREError

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_TELEGRAM_MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class AlarmCredentials:
    # repr=False so the auto-generated __repr__ does not leak the token into
    # pytest assertion output, tracebacks, or structured log capture.
    bot_token: str = field(repr=False)
    chat_id: str = field()


def _telegram_store_config() -> dict[str, object]:
    """Return the Telegram integration's effective config, or ``{}``.

    Reads the merged integration store + environment view used everywhere else
    (investigation pipeline, scheduler). Returns an empty mapping when the store
    is unavailable or has no Telegram integration so callers fall back to the
    environment / keyring. Resolution is wrapped defensively: a malformed or
    locked store must never crash the watchdog at startup.
    """
    try:
        from integrations.catalog import resolve_effective_integrations

        entry = resolve_effective_integrations().get("telegram", {})
        config = entry.get("config", {}) if isinstance(entry, dict) else {}
        return config if isinstance(config, dict) else {}
    except Exception:
        logger.debug("Failed to resolve Telegram credentials from the store", exc_info=True)
        return {}


def _resolve_bot_token(store_config: dict[str, object]) -> str:
    """Resolve the bot token: store first, then ``TELEGRAM_BOT_TOKEN`` env, then keyring."""
    store_token = str(store_config.get("bot_token") or "").strip()
    if store_token:
        return store_token
    # resolve_env_credential checks the environment first, then the system
    # keyring — so guided setup (which stores the token in the keyring) works.
    from config.llm_credentials import resolve_env_credential

    return resolve_env_credential("TELEGRAM_BOT_TOKEN").strip()


def _resolve_chat_id(store_config: dict[str, object], chat_id_override: str | None) -> str:
    """Resolve the chat id: ``--chat-id`` override, then store, then env."""
    # Strip first so a whitespace-only override falls back consistently with an
    # empty-string override, instead of raising a misleading "pass --chat-id"
    # error after the caller already passed one.
    stripped_override = chat_id_override.strip() if chat_id_override else ""
    if stripped_override:
        return stripped_override
    store_chat_id = str(store_config.get("default_chat_id") or "").strip()
    if store_chat_id:
        return store_chat_id
    return os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "").strip()


def load_credentials_from_env(
    *,
    chat_id_override: str | None = None,
) -> AlarmCredentials:
    """Resolve Telegram credentials from the integration store, env, or keyring.

    Resolution order (matching the scheduler):

    * **bot token** — integration store → ``TELEGRAM_BOT_TOKEN`` env → system keyring
    * **chat id** — ``--chat-id`` override → store ``default_chat_id`` →
      ``TELEGRAM_DEFAULT_CHAT_ID`` env

    The name is retained for backward compatibility; resolution is no longer
    env-only, so credentials saved by ``opensre onboard`` /
    ``opensre integrations setup telegram`` work for the watchdog and Hermes
    instead of raising ``TELEGRAM_BOT_TOKEN is not set``.
    """
    store_config = _telegram_store_config()

    bot_token = _resolve_bot_token(store_config)
    if not bot_token:
        raise OpenSREError(
            "TELEGRAM_BOT_TOKEN is not set.",
            suggestion=(
                "Configure Telegram with `opensre integrations setup telegram` "
                "(or `opensre onboard`), or export TELEGRAM_BOT_TOKEN=<your-bot-token>. "
                "Get a token from @BotFather on Telegram."
            ),
        )

    chat_id = _resolve_chat_id(store_config, chat_id_override)
    if not chat_id:
        raise OpenSREError(
            "Telegram chat id is not set.",
            suggestion=(
                "Set a default chat id during `opensre integrations setup telegram`, "
                "export TELEGRAM_DEFAULT_CHAT_ID=<chat-id>, or pass --chat-id and retry."
            ),
        )

    return AlarmCredentials(bot_token=bot_token, chat_id=chat_id)


class AlarmDispatcher:
    """Dispatch watchdog alarms to Telegram with per-threshold cooldown."""

    def __init__(
        self,
        creds: AlarmCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        parse_mode: str = "",
    ) -> None:
        self._creds = creds
        self._cooldown_seconds = cooldown_seconds
        self._parse_mode = parse_mode
        self._last_dispatched: dict[str, float] = {}
        self._lock = threading.Lock()

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Telegram unless this threshold is in cooldown."""
        now = self._now()

        # Reserve the cooldown slot under the lock BEFORE the network call so
        # a concurrent dispatch on the same threshold sees the reservation and
        # is suppressed. Without this, two threads could both pass the check
        # (state of last_dispatched at "check" time != "use" time, classic
        # TOCTOU) and both send.
        with self._lock:
            last = self._last_dispatched.get(threshold_name)
            if last is not None and (now - last) < self._cooldown_seconds:
                logger.debug(
                    "[watchdog] alarm suppressed by cooldown: name=%s remaining=%.1fs",
                    threshold_name,
                    self._cooldown_seconds - (now - last),
                )
                return False
            self._last_dispatched[threshold_name] = now

        if self._parse_mode.upper() == "HTML":
            text = truncate_for_telegram_html(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…")
        else:
            text = truncate(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…")

        ok, error, _ = post_telegram_message(
            chat_id=self._creds.chat_id,
            text=text,
            bot_token=self._creds.bot_token,
            parse_mode=self._parse_mode,
        )
        if ok:
            return True

        # Roll back the reservation only if it's still ours, so a transient
        # failure does not silently swallow the next real alarm. Compare-and-
        # delete prevents stomping on a parallel successful dispatch that
        # may have updated the slot in the meantime.
        with self._lock:
            if self._last_dispatched.get(threshold_name) == now:
                del self._last_dispatched[threshold_name]

        logger.warning(
            "[watchdog] alarm delivery failed: name=%s error=%s",
            threshold_name,
            error,
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
