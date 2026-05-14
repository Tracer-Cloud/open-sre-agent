"""Shared Sentry routing for ``make_*_client`` factory failures (#1459).

Historically, every ``make_<vendor>_client`` factory in ``app/services/<vendor>/``
collapsed both "integration not configured" (missing fields) and "integration
broken" (construction blew up — invalid URL, refused validator, bad type)
into the same ``None`` return value. Callers wrote::

    client = make_argocd_client(config)
    if client is None:
        return  # treat as "not configured"

…so a real config bug looked identical to a deliberately empty config, and
the failure never reached Sentry.

This module routes the *broken* path through ``app.utils.errors.report_exception``
while preserving the historic caller contract (``None`` return). The ``not
configured`` branches that pre-check required fields still return ``None``
silently — silent is correct there, because absent config is intentional.

When ``app/services/_base.py`` lands from #1458 with its typed
``ServiceClientUnavailable`` exception, callers can graduate to a typed
contract and this module can be folded in.
"""

from __future__ import annotations

import logging

from app.utils.errors import report_exception


def report_factory_failure(
    exc: BaseException,
    *,
    integration: str,
    logger: logging.Logger,
) -> None:
    """Route a ``make_<vendor>_client`` construction failure to Sentry + logs.

    Tag set matches the convention from #1454 / merged #1468:

      surface     = service_client
      component   = app.services.<integration>.client
      integration = <vendor>
      event       = factory_failure

    Severity is ``warning`` — most factory failures we see in the wild are
    misconfigured credentials (an HTTPS URL with a typo, a malformed bearer
    token), not service bugs. They deserve a Sentry event so operators can
    distinguish "not configured" from "configured incorrectly", but they
    should not page on-call alongside real internal errors.
    """
    report_exception(
        exc,
        logger=logger,
        message=f"{integration} client construction failed; caller will treat as unavailable",
        severity="warning",
        tags={
            "surface": "service_client",
            "component": f"app.services.{integration}.client",
            "integration": integration,
            "event": "factory_failure",
        },
    )
