"""Gateway process and turn machinery: lifecycle, dispatch, sink contracts.

Composition root: :mod:`gateway.runtime.manager` (``GatewayManager``).
Package entry: ``python -m gateway.main`` → :mod:`gateway.main` → ``manager.main``.
Production entry with slash ports: ``python -m surfaces.cli.gateway_entry``.
Shared contracts: :mod:`gateway.runtime.sink_protocol`, :mod:`gateway.runtime.errors`.
"""

from __future__ import annotations
