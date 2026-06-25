"""Probe-style verifier helpers.

The probe shape: build a typed config, instantiate a vendor SDK
client, call ``client.probe_access()``. Used by the majority of
integrations that have a remote endpoint to hit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.integrations.verification.registry import VerifierFn, register_verifier


def result(
    service: str,
    source: str,
    status: str,
    detail: str,
) -> dict[str, str]:
    """Standard verifier return shape — every per-vendor module uses this."""
    return {
        "service": service,
        "source": source,
        "status": status,
        "detail": detail,
    }


def build_probe_verifier[ConfigT](
    service: str,
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    client_factory: Callable[[ConfigT], Any],
) -> VerifierFn:
    """Construct a verifier that builds a client and calls ``probe_access()``.

    The common pattern across most vendors: validate config, instantiate
    the client, call ``probe_access()``. Returning a factory function
    keeps each per-vendor verifier module to ~5 lines of declaration.
    """

    def _verifier(source: str, config: dict[str, Any]) -> dict[str, str]:
        try:
            normalized_config = build_config(config)
        except Exception as err:
            return result(service, source, "missing", str(err))
        try:
            probe_result = client_factory(normalized_config).probe_access()
        except Exception as err:
            return result(service, source, "failed", str(err))
        return result(service, source, probe_result.status, probe_result.detail)

    return _verifier


def register_probe_verifier[ConfigT](
    service: str,
    *,
    config: Callable[[dict[str, Any]], ConfigT],
    client: Callable[[ConfigT], Any],
) -> VerifierFn:
    """Build a probe-style verifier and register it in one call.

    Replaces the verbose three-layer idiom::

        verify_X = register_verifier("X")(build_probe_verifier(
            "X", build_config=..., client_factory=...))

    with one self-contained call::

        register_probe_verifier("X", config=..., client=...)

    Returns the registered verifier so callers that want to keep a
    module-level handle can ``verify_X = register_probe_verifier(...)``
    — but the side effect (registration) is the contract.
    """
    return register_verifier(service)(
        build_probe_verifier(service, build_config=config, client_factory=client)
    )
