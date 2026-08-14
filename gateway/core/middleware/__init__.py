"""Shared per-turn steps every chat transport runs.

Middleware in the API-framework sense: the work between "a platform event
arrived" (a transport controller) and "run the turn" (the service layer's
turn handler). Each module is one step; transports compose them and add only
platform specifics. See :mod:`gateway.core.transport_api` for the full map.
"""

from __future__ import annotations

__all__: list[str] = []
