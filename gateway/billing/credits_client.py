"""Consume opensre-webapp credits before metered agent work.

Contract:
  POST {OPENSRE_WEBAPP_URL}/api/credits/consume
  Authorization: Bearer <machine token, or the shared secret>
  body: {"amount": <number>, "organizationId": <org>, "reason": <str>}
  Success (2xx): {"balance", "consumed", "reason"}.
  Shortfall: HTTP 402 with {"error": "insufficient_credits", "balance", "required"}.

The client only classifies the attempt — it never decides policy. Call sites
choose what UNCONFIGURED (metering off, e.g. dev setups) and UNAVAILABLE
(webapp outage) mean; the gateway seams deliberately fail open on both.
"""

from __future__ import annotations

import functools
import logging
import os
from enum import Enum
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.billing import (
    CREDITS_HTTP_TIMEOUT_SECONDS,
    ORGANIZATION_ID_ENV,
    WEBAPP_URL_ENV,
)
from integrations.slack.webapp_auth import webapp_bearer_token

logger = logging.getLogger(__name__)

_CONSUME_PATH = "/api/credits/consume"


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


@functools.cache
def _log_metering_disabled_once() -> None:
    """Log once per process that metering is off because config is incomplete.

    The message is static — no env value or name is interpolated — so the
    warning can never carry a secret into the logs.
    """
    logger.info("[credits] metering disabled: required configuration is not fully set")


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
    token = webapp_bearer_token()
    org = (organization_id or organization_id_for_silo()).strip()

    if not (base_url and token and org):
        _log_metering_disabled_once()
        return CreditsOutcome.UNCONFIGURED

    # Metadata is spread first so the billing-critical fields always win and can
    # never be overwritten by a supplemental key.
    payload: dict[str, Any] = {
        **(metadata or {}),
        "amount": amount,
        "organizationId": org,
        "reason": reason,
    }

    try:
        response = httpx.post(
            f"{base_url}{_CONSUME_PATH}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "[credits] webapp unreachable for reason=%s (%s: %s)",
            reason,
            type(exc).__name__,
            exc,
        )
        return CreditsOutcome.UNAVAILABLE

    return _classify_response(response, reason=reason)


def _classify_response(response: httpx.Response, *, reason: str) -> CreditsOutcome:
    """Map a ledger HTTP response to an outcome.

    402 → DENIED (the one refuse-the-user state); 2xx → ALLOWED; anything else
    → UNAVAILABLE, so callers fail open on server errors.
    """
    if response.status_code == HTTPStatus.PAYMENT_REQUIRED:
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
