"""Adapter: wire ``TracerClient.get_all_integrations`` into the
:mod:`integrations.port` ``RemoteIntegrationsFetcher`` port.

Lives in ``integrations/tracer/`` so the Tracer-specific dependency stays
inside the Tracer integration package. Core code
under ``core/orchestration/`` calls
:func:`integrations.port.fetch_remote_integrations`; the boundary
(``interactive_shell.ui.output.boundary``) registers this
adapter at startup so the call routes through ``TracerClient``.
"""

from __future__ import annotations

from typing import Any

from integrations.tracer import get_tracer_client_for_org


def fetch_tracer_remote_integrations(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    """Fetch a user's remote integrations from Tracer Cloud.

    Matches :data:`integrations.port.RemoteIntegrationsFetcher`.
    Any exception (network, auth, schema) propagates to the caller —
    ``resolve_integrations`` already has the try/except + local
    fall-through logic.
    """
    return get_tracer_client_for_org(org_id, auth_token).get_all_integrations()
