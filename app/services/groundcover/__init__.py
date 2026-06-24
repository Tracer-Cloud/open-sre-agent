"""groundcover service client package.

Exposes the single transport layer (:class:`GroundcoverClient`) that every
groundcover OpenSRE tool depends on. All MCP/JSON-RPC/SSE wire details live in
``client.py``; tools call typed methods and never build protocol payloads.
"""

from __future__ import annotations

from app.services.groundcover.client import (
    GroundcoverClient,
    GroundcoverConfig,
    GroundcoverToolResult,
)

__all__ = [
    "GroundcoverClient",
    "GroundcoverConfig",
    "GroundcoverToolResult",
]
