"""Resolve integrations node — integration discovery for investigations.

Node contract:
    Entrypoint : resolve_integrations(state: InvestigationState) -> dict[str, Any]
    Reads      : _auth_token, org_id,
                 resolved_integrations (idempotency guard — skips if already set)
    Writes     : resolved_integrations
"""

from core.orchestration.node.resolve_integrations.node import (
    resolve_integrations,
    resolve_integrations_quiet,
)

__all__ = ["resolve_integrations", "resolve_integrations_quiet"]
