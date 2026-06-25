"""Credential resolution for scheduled task delivery.

Resolves provider credentials from the integration store and environment
rather than requiring them to be stored in task params.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def resolve_telegram_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Telegram bot_token from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    bot_token = task_params.get("bot_token", "")
    if bot_token:
        return {"bot_token": bot_token}

    # Try integration store
    bot_token = _get_integration_credential("telegram", "bot_token")
    if bot_token:
        return {"bot_token": bot_token}

    # Fall back to environment
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if bot_token:
        return {"bot_token": bot_token}

    return {}


def resolve_slack_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Slack access_token from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    access_token = task_params.get("access_token", "")
    if access_token:
        return {"access_token": access_token}

    # Try integration store
    access_token = _get_integration_credential("slack", "access_token")
    if access_token:
        return {"access_token": access_token}

    # Fall back to environment
    access_token = os.getenv("SLACK_BOT_TOKEN", "") or os.getenv("SLACK_ACCESS_TOKEN", "")
    if access_token:
        return {"access_token": access_token}

    return {}


def resolve_discord_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Discord bot_token from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    bot_token = task_params.get("bot_token", "")
    if bot_token:
        return {"bot_token": bot_token}

    # Try integration store
    bot_token = _get_integration_credential("discord", "bot_token")
    if bot_token:
        return {"bot_token": bot_token}

    # Fall back to environment
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
    if bot_token:
        return {"bot_token": bot_token}

    return {}


def _get_integration_credential(service: str, key: str) -> str:
    """Look up a credential from the integration store."""
    try:
        from integrations.catalog import resolve_effective_integrations

        integrations = resolve_effective_integrations()
        integration: dict[str, Any] = integrations.get(service, {})
        if not isinstance(integration, dict):
            return ""
        config = integration.get("config", {})
        if not isinstance(config, dict):
            return ""
        value = config.get(key, "")
        return str(value) if value else ""
    except Exception:
        logger.debug("Failed to resolve %s credential from integration store", service)
        return ""


__all__ = [
    "resolve_discord_credentials",
    "resolve_slack_credentials",
    "resolve_telegram_credentials",
]
