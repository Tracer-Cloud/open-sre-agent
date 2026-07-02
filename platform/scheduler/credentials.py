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
    return _resolve_credentials(
        task_params,
        service="telegram",
        credential_key="bot_token",
        env_vars=("TELEGRAM_BOT_TOKEN",),
    )


def resolve_slack_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Slack access_token from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    return _resolve_credentials(
        task_params,
        service="slack",
        credential_key="access_token",
        env_vars=("SLACK_BOT_TOKEN", "SLACK_ACCESS_TOKEN"),
    )


def resolve_discord_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Discord bot_token from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    return _resolve_credentials(
        task_params,
        service="discord",
        credential_key="bot_token",
        env_vars=("DISCORD_BOT_TOKEN",),
    )


def _resolve_credentials(
    task_params: dict[str, str],
    *,
    service: str,
    credential_key: str,
    env_vars: tuple[str, ...],
) -> dict[str, str]:
    """Resolve a single credential from task params, integration store, or env."""
    value = task_params.get(credential_key, "")
    if value:
        return {credential_key: value}

    value = _get_integration_credential(service, credential_key)
    if value:
        return {credential_key: value}

    for env_var in env_vars:
        value = os.getenv(env_var, "")
        if value:
            return {credential_key: value}

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
