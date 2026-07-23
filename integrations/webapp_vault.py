"""Fetch org integration credentials from opensre-webapp (silo → vault).

Contract mirrors credits metering:
  GET {OPENSRE_WEBAPP_URL}/api/agent/integrations?organizationId=…
  Authorization: Bearer {mt_… from CLERK_MACHINE_SECRET_KEY}
  Success: {"success": true, "data": [{id, service, status, name, credentials}, …]}

This route accepts an M2M token only. The shared secret cannot prove which
tenant is asking, so a request carrying it gets 401.

Used by the gateway when resolving integrations for Slack/Telegram turns so
org-admins can connect GitHub (etc.) in the webapp without SSM per secret.
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.billing import (
    CREDITS_HTTP_TIMEOUT_SECONDS,
    MACHINE_SECRET_ENV,
    ORGANIZATION_ID_ENV,
    USAGE_SECRET_ENV,
    WEBAPP_URL_ENV,
)
from integrations.slack.agent_auth import agent_auth_token

logger = logging.getLogger(__name__)

_INTEGRATIONS_PATH = "/api/agent/integrations"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def webapp_vault_configured() -> bool:
    """True when silo env has everything needed to call the webapp vault.

    Checks that *a* credential is configured rather than resolving one, so this
    stays a cheap env read — resolving would mint an M2M token over the network.
    """
    has_credential = bool(_env(MACHINE_SECRET_ENV) or _env(USAGE_SECRET_ENV))
    return bool(_env(WEBAPP_URL_ENV) and has_credential and _env(ORGANIZATION_ID_ENV))


def fetch_webapp_org_integrations(
    organization_id: str | None = None,
) -> list[dict[str, Any]] | None:
    """Return active vault integrations for the silo org, or ``None`` if unavailable.

    ``None`` means "do not treat as an empty remote" — caller should fall through
    to local/env. An empty list means the org has no exportable integrations.
    """
    base_url = _env(WEBAPP_URL_ENV).rstrip("/")
    token = agent_auth_token()
    org = (organization_id or _env(ORGANIZATION_ID_ENV)).strip()
    if not (base_url and token and org):
        return None

    url = f"{base_url}{_INTEGRATIONS_PATH}"
    try:
        response = httpx.get(
            url,
            params={"organizationId": org},
            headers={"Authorization": f"Bearer {token}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("[webapp-vault] request failed", exc_info=True)
        return None

    if response.status_code != HTTPStatus.OK:
        logger.warning(
            "[webapp-vault] HTTP %s from integrations vault",
            response.status_code,
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("[webapp-vault] non-JSON response")
        return None

    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        service = str(item.get("service") or "").strip()
        credentials = item.get("credentials")
        if not service or not isinstance(credentials, dict):
            continue
        records.append(
            {
                "id": str(item.get("id") or ""),
                "service": service,
                "status": str(item.get("status") or "active"),
                "name": str(item.get("name") or "default"),
                "credentials": {str(k): str(v) for k, v in credentials.items() if v is not None},
            }
        )
    return records
