"""Gateway process and turn machinery: lifecycle, dispatch, sink contracts.

Composition root: :mod:`gateway.core.runtime.controller` (``GatewayController``).
Production boot: ``opensre gateway start`` (CLI injects slash ports).
Package ``python -m gateway`` fails closed.
Shared contracts: :mod:`gateway.core.transport_api`, :mod:`gateway.core.runtime.errors`.
"""

from __future__ import annotations
