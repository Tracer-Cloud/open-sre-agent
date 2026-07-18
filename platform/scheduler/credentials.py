"""Credential resolution for scheduled task delivery.

Resolves provider credentials from the integration store and environment
rather than requiring them to be stored in task params.

Secret env vars use ``resolve_env_credential`` (process env, then OS keyring)
so wizard ``sync_env_secret`` writes are visible when the store is empty.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config.llm_credentials import resolve_env_credential

logger = logging.getLogger(__name__)


def resolve_telegram_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Telegram bot_token from task params, integration store, env, or keyring.

    Priority: task.params > integration store > environment variable > system keyring.
    """
    return _resolve_credentials(
        task_params,
        service="telegram",
        credential_key="bot_token",
        env_vars=("TELEGRAM_BOT_TOKEN",),
    )


def resolve_slack_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Slack credentials from task params, integration store, env, or keyring.

    Priority: task.params > integration store > environment variable > system keyring.
    Webhook URLs stay env/store only (not keyring-eligible as ``*_URL``).
    """
    webhook_url = task_params.get("webhook_url", "").strip()
    if webhook_url:
        return {"webhook_url": webhook_url}

    access_token = task_params.get("access_token", "").strip()
    if access_token:
        return {"access_token": access_token}

    # Webhook: store then plain env — never resolve_env_credential / keyring.
    store_webhook = _get_integration_credential("slack", "webhook_url").strip()
    if store_webhook:
        return {"webhook_url": store_webhook}
    env_webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if env_webhook:
        return {"webhook_url": env_webhook}

    return _resolve_credentials(
        {},
        service="slack",
        credential_key="access_token",
        env_vars=("SLACK_BOT_TOKEN", "SLACK_ACCESS_TOKEN"),
    )


def resolve_discord_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Discord bot_token from task params, integration store, env, or keyring.

    Priority: task.params > integration store > environment variable > system keyring.
    """
    return _resolve_credentials(
        task_params,
        service="discord",
        credential_key="bot_token",
        env_vars=("DISCORD_BOT_TOKEN",),
    )


def resolve_rocketchat_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Rocket.Chat credentials from task params, integration store, or env.

    Priority: task.params > integration store > environment variable (then keyring
    for the PAT), applied per key. Returns whichever of
    ``server_url``/``auth_token``/``user_id`` (token mode) and ``webhook_url``
    (webhook mode) could be resolved; the executor decides whether the
    combination is usable.

    Webhook URLs stay store/env only — never ``resolve_env_credential`` / keyring
    (same rule as Slack ``SLACK_WEBHOOK_URL``).
    """
    resolved: dict[str, str] = {}

    # Non-secret / non-keyring fields: params → store → plain env.
    for key, env_var in (
        ("server_url", "ROCKETCHAT_SERVER_URL"),
        ("user_id", "ROCKETCHAT_USER_ID"),
    ):
        value = task_params.get(key, "").strip()
        if not value:
            value = _get_integration_credential("rocketchat", key).strip()
        if not value:
            value = os.getenv(env_var, "").strip()
        if value:
            resolved[key] = value

    # PAT: params → store → env then keyring.
    auth = _resolve_credentials(
        task_params,
        service="rocketchat",
        credential_key="auth_token",
        env_vars=("ROCKETCHAT_AUTH_TOKEN",),
    )
    resolved.update(auth)

    # Webhook: params → store → plain env only.
    webhook_url = task_params.get("webhook_url", "").strip()
    if not webhook_url:
        webhook_url = _get_integration_credential("rocketchat", "webhook_url").strip()
    if not webhook_url:
        webhook_url = os.getenv("ROCKETCHAT_WEBHOOK_URL", "").strip()
    if webhook_url:
        resolved["webhook_url"] = webhook_url

    return resolved


def _resolve_credentials(
    task_params: dict[str, str],
    *,
    service: str,
    credential_key: str,
    env_vars: tuple[str, ...],
) -> dict[str, str]:
    """Resolve a single credential from task params, store, env, or keyring."""
    value = task_params.get(credential_key, "")
    if value:
        return {credential_key: value}

    value = _get_integration_credential(service, credential_key)
    if value:
        return {credential_key: value}

    for env_var in env_vars:
        value = resolve_env_credential(env_var).strip()
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
    "resolve_rocketchat_credentials",
    "resolve_slack_credentials",
    "resolve_telegram_credentials",
]
