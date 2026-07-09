"""Credential resolution for scheduled task delivery.

Resolves provider credentials from the integration store and environment
rather than requiring them to be stored in task params.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


try:
    from keyring.errors import KeyringError as _KeyringError
except ImportError:

    class _KeyringError(Exception):  # type: ignore[no-redef]
        """Fallback when keyring is not installed."""


def resolve_telegram_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Telegram bot_token from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    try:
        from integrations.telegram.credentials import resolve_telegram_bot_token

        token = resolve_telegram_bot_token(task_params)
        return {"bot_token": token} if token else {}
    except (
        ImportError,
        KeyError,
        TypeError,
        ValueError,
        _KeyringError,
    ) as exc:
        logger.debug("Failed to resolve Telegram credentials: %s", exc)
        return {}


def resolve_slack_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Slack credentials from task params, integration store, or env.

    Priority: task.params > integration store > environment variable.
    """
    webhook_url = task_params.get("webhook_url", "")
    if webhook_url:
        return {"webhook_url": webhook_url}

    access_token = task_params.get("access_token", "")
    if access_token:
        return {"access_token": access_token}

    webhook = _resolve_credentials(
        {},
        service="slack",
        credential_key="webhook_url",
        env_vars=("SLACK_WEBHOOK_URL",),
    )
    if webhook:
        return webhook

    return _resolve_credentials(
        {},
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
