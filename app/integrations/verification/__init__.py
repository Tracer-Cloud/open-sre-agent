"""Verification plugin registry — vendor-agnostic decorator + lookup.

Each per-vendor verifier registers itself via :func:`register_verifier`
(or the higher-order helpers :func:`register_probe_verifier` and
:func:`register_validation_verifier` for the two common shapes).
``app.integrations.registry`` and ``app.integrations.verify`` query
the registry instead of importing every verifier by name. Adding a new
vendor becomes a single new ``app/integrations/verifiers/<vendor>.py``
file with one registration call — the loader auto-discovers it.
"""

from __future__ import annotations

from app.integrations.verification.probe import (
    build_probe_verifier,
    register_probe_verifier,
    result,
)
from app.integrations.verification.registry import (
    VerifierFn,
    get_verifier,
    list_verifiers,
    register_verifier,
)
from app.integrations.verification.validation import (
    build_validation_verifier,
    register_validation_verifier,
    verify_with_validation_result,
)

__all__ = [
    "VerifierFn",
    "build_probe_verifier",
    "build_validation_verifier",
    "get_verifier",
    "list_verifiers",
    "register_probe_verifier",
    "register_validation_verifier",
    "register_verifier",
    "result",
    "verify_with_validation_result",
]
