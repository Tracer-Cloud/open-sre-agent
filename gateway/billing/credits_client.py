"""Consume opensre-webapp credits before metered agent work.

Contract:
  POST {OPENSRE_WEBAPP_URL}/api/credits/consume
  Authorization: Bearer {AGENT_USAGE_SECRET}
  body: {"amount": <number>, "organizationId": <org>, "reason": <str>}
  Success (2xx): {"balance", "consumed", "reason"}.
  Shortfall: HTTP 402 with {"error": "insufficient_credits", "balance", "required"}.

The client only classifies the attempt — it never decides policy. Call sites
choose what UNCONFIGURED (metering off, e.g. dev setups) and UNAVAILABLE
(webapp outage) mean; the gateway seams deliberately fail open on both.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Env vars injected by the org-silo infra (ECS task definition).
WEBAPP_URL_ENV = "OPENSRE_WEBAPP_URL"
USAGE_SECRET_ENV = "AGENT_USAGE_SECRET"
ORGANIZATION_ID_ENV = "OPENSRE_ORGANIZATION_ID"

_TIMEOUT_SECONDS = 5.0

# Metering-disabled is logged once per process, not once per turn.
_unconfigured_logged = False


class CreditsOutcome(Enum):
    """Classification of one credit-consume attempt; policy belongs to callers."""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNCONFIGURED = "unconfigured"
    UNAVAILABLE = "unavailable"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def organization_id_for_silo() -> str:
    """Neon/Clerk organization id for this silo (statically injected by infra)."""
    return _env(ORGANIZATION_ID_ENV)


def _log_unconfigured_once(missing: str) -> None:
    global _unconfigured_logged
    if _unconfigured_logged:
        return
    _unconfigured_logged = True
    logger.info("[credits] metering disabled: %s not set", missing)


def consume_credits(
    organization_id: str | None = None,
    *,
    amount: float = 1.0,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> CreditsOutcome:
    """POST one credit consumption to the webapp ledger and classify the result.

    Args:
        organization_id: Neon/Clerk org id; defaults to the silo's
            ``OPENSRE_ORGANIZATION_ID`` env value.
        amount: Credits to consume (webapp requires a positive number).
        reason: Short machine-readable cause, e.g. ``"slack_turn"``.
        metadata: Optional extra JSON fields merged into the request body.

    Returns:
        ``ALLOWED`` on 2xx, ``DENIED`` on HTTP 402, ``UNCONFIGURED`` when the
        webapp URL / shared secret / org id is unset, ``UNAVAILABLE`` on
        transport errors or any other HTTP status.
    """
    base_url = _env(WEBAPP_URL_ENV).rstrip("/")
    secret = _env(USAGE_SECRET_ENV)
    org = (organization_id or organization_id_for_silo()).strip()
    missing = [
        name
        for name, value in (
            (WEBAPP_URL_ENV, base_url),
            (USAGE_SECRET_ENV, secret),
            (ORGANIZATION_ID_ENV, org),
        )
        if not value
    ]
    if missing:
        _log_unconfigured_once(", ".join(missing))
        return CreditsOutcome.UNCONFIGURED

    payload: dict[str, Any] = {"amount": amount, "organizationId": org, "reason": reason}
    if metadata:
        payload.update(metadata)

    try:
        response = httpx.post(
            f"{base_url}/api/credits/consume",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "[credits] webapp unreachable for reason=%s (%s: %s)",
            reason,
            type(exc).__name__,
            exc,
        )
        return CreditsOutcome.UNAVAILABLE

    if response.status_code == 402:
        body = _json_dict(response)
        logger.info(
            "[credits] denied reason=%s balance=%s required=%s",
            reason,
            body.get("balance"),
            body.get("required"),
        )
        return CreditsOutcome.DENIED
    if response.is_success:
        return CreditsOutcome.ALLOWED
    logger.warning("[credits] webapp HTTP %s for reason=%s", response.status_code, reason)
    return CreditsOutcome.UNAVAILABLE


def _json_dict(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
