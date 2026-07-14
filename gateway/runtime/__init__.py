"""Gateway process and turn machinery: lifecycle, dispatch, sink contracts.

Composition root: :mod:`gateway.runtime.manager` (``GatewayManager``).
Package entry: ``python -m gateway.main`` → :mod:`gateway.main` → ``manager.main``.
Shared contracts: :mod:`gateway.runtime.sink_protocol`, :mod:`gateway.runtime.errors`.
"""

from __future__ import annotations
