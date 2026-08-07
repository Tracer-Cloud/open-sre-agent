"""Gateway process and turn machinery: lifecycle, dispatch, sink contracts.

Composition root: :mod:`gateway.core.runtime.manager` (``GatewayManager``).
Production boot: ``opensre gateway start`` (CLI injects slash ports).
Package ``python -m gateway.main`` and bare ``manager.main`` fail closed.
Shared contracts: :mod:`gateway.core.runtime.sink_protocol`, :mod:`gateway.core.runtime.errors`.
"""

from __future__ import annotations
