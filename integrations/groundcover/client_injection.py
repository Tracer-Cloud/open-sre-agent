"""Config extraction and client injection for groundcover tools."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations._validation_helpers import report_validation_failure
from integrations.groundcover.client import GroundcoverClient, GroundcoverConfig

logger = logging.getLogger(__name__)

def groundcover_creds(gc: dict[str, Any]) -> dict[str, Any]:
    """Extract api_key/mcp_url/tenant_uuid/backend_id/timezone from a source entry."""
    return {
        "api_key": gc.get("api_key", ""),
        "mcp_url": gc.get("mcp_url", ""),
        "tenant_uuid": gc.get("tenant_uuid", ""),
        "backend_id": gc.get("backend_id", ""),
        "timezone": gc.get("timezone", "UTC"),
    }



def make_client(creds: dict[str, Any]) -> GroundcoverClient | None:
    """Build a GroundcoverClient, or None when credentials are missing/invalid."""
    if not creds.get("api_key"):
        return None
    try:
        config = GroundcoverConfig.model_validate(creds)
    except ValidationError:
        return None
    except Exception as exc:
        report_validation_failure(
            exc,
            logger=logger,
            integration="groundcover",
            method="make_client",
        )
        return None
    if not config.is_configured:
        return None
    return GroundcoverClient(config)



def client_for_source(gc: dict[str, Any]) -> GroundcoverClient | None:
    """Build a GroundcoverClient from a resolved ``groundcover`` source entry."""
    return make_client(groundcover_creds(gc))



def base_extract_params(
    gc: dict[str, Any],
    *,
    default_query: str | None = None,
    include_period: bool = True,
) -> dict[str, Any]:
    """Inject a pre-built client + optional fixture backend, never raw secrets.

    Credentials are bound here into a runtime ``GroundcoverClient`` object so the
    model never sees or can override them. The ``_groundcover_client`` and
    ``groundcover_backend`` keys are runtime objects that the seed-input
    redactor (``^_`` / ``*backend`` patterns) strips before schema validation.
    Only real objects (and schema-declared fields) are included so
    ``additionalProperties: false`` schemas accept the seed input. Tools without
    a time window (entities/monitors/reference) pass ``include_period=False``.
    """
    params: dict[str, Any] = {}
    if include_period:
        params["period"] = gc.get("period", "PT1H")
    if default_query is not None:
        params["query"] = gc.get("default_query") or default_query
    backend = gc.get("_backend")
    if backend is not None:
        params["groundcover_backend"] = backend
    client = client_for_source(gc)
    if client is not None:
        params["_groundcover_client"] = client
    return params
