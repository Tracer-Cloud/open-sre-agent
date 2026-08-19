"""Shared per-turn steps every chat transport runs.

Middleware in the API-framework sense: the work between "a platform event
arrived" (a transport controller) and "run the turn" (the service layer's
turn handler). Each module is one step; transports compose them and add only
platform specifics. Turn output lives in :mod:`gateway.core.host.turn_output`;
the transport registry lives in :mod:`gateway.transports`.
"""

from __future__ import annotations

__all__: list[str] = []
