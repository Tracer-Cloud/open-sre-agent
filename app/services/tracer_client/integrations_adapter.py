"""Adapter: wire ``TracerClient.get_all_integrations`` into the
:mod:`app.integrations.port` ``RemoteIntegrationsFetcher`` port.

Lives in ``app/services/tracer_client/`` so the Tracer-specific
dependency stays inside the Tracer integration package. Core code
under ``app/core/orchestration/`` calls
:func:`app.integrations.port.fetch_remote_integrations`; the boundary
(``app.cli.interactive_shell.ui.output.boundary``) registers this
adapter at startup so the call routes through ``TracerClient``.
"""

from __future__ import annotations

from typing import Any

from app.services.tracer_client import get_tracer_client_for_org


def fetch_tracer_remote_integrations(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    """Fetch a user's remote integrations from Tracer Cloud.

    Matches :data:`app.integrations.port.RemoteIntegrationsFetcher`.
    Any exception (network, auth, schema) propagates to the caller —
    ``resolve_integrations`` already has the try/except + local
    fall-through logic.
    """
    return get_tracer_client_for_org(org_id, auth_token).get_all_integrations()
