"""Integration provider ports — abstractions core code uses to fetch
remote integration configuration without depending on a specific
SaaS backend.

Today the only port is :func:`fetch_remote_integrations`, called by
:mod:`core.orchestration.node.resolve_integrations.node` to pull the user's
org-wide integrations from a remote source. The default returns an
empty list; the CLI/SDK boundary registers a Tracer-Cloud-backed
fetcher via :func:`set_remote_integrations_fetcher`. Headless
contexts (tests, scripted invocations, alternate hosting) get the
empty default and fall through to local-store integration sources.

Same Ports & Adapters pattern as
``core.orchestration.node.publish_findings.upstream_correlation`` and
``platform/observability``: core
depends on abstractions, vendor SDKs plug in as adapters at the
boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

RemoteIntegrationsFetcher = Callable[[str, str], list[dict[str, Any]]]


def _default_fetcher(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    """No remote source available — caller falls through to local sources."""
    _ = (org_id, auth_token)
    return []


_fetcher: RemoteIntegrationsFetcher = _default_fetcher


def fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
    """Fetch the user's remote integrations via the registered fetcher.

    Returns the integration list as raw dicts (same shape the local
    store uses), or an empty list if no fetcher is registered. Empty
    is a meaningful signal: caller should fall through to local
    sources rather than error.
    """
    return _fetcher(org_id, auth_token)


def set_remote_integrations_fetcher(fetcher: RemoteIntegrationsFetcher) -> None:
    """Install ``fetcher`` as the active remote-integrations implementation.

    Boundary code (typically the CLI start-up) calls this to wire a
    Tracer-Cloud-backed fetcher in place of the empty default.
    """
    global _fetcher
    _fetcher = fetcher
