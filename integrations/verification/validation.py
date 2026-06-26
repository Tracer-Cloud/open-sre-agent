"""Validation-style verifier helper.

Sibling of ``probe.py``. The validation shape calls a pure
``validate_<vendor>_config(...)`` function that returns a result object
with ``ok: bool`` and ``detail: str``. Used by config-only integrations
that have no remote SDK client to probe.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from integrations.verification.probe import result
from integrations.verification.registry import VerifierFn, register_verifier


def verify_with_validation_result[ConfigT](
    service: str,
    source: str,
    config: dict[str, Any],
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    validate_config: Callable[[ConfigT], Any],
) -> dict[str, str]:
    try:
        normalized_config = build_config(config)
    except Exception as err:
        return result(service, source, "missing", str(err))
    try:
        validation_result = validate_config(normalized_config)
    except Exception as err:
        return result(service, source, "failed", str(err))
    return result(
        service,
        source,
        "passed" if validation_result.ok else "failed",
        validation_result.detail,
    )


def build_validation_verifier[ConfigT](
    service: str,
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    validate_config: Callable[[ConfigT], Any],
) -> VerifierFn:
    def _verifier(source: str, config: dict[str, Any]) -> dict[str, str]:
        return verify_with_validation_result(
            service,
            source,
            config,
            build_config=build_config,
            validate_config=validate_config,
        )

    return _verifier


def register_validation_verifier[ConfigT](
    service: str,
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    validate_config: Callable[[ConfigT], Any],
) -> VerifierFn:
    """Build a validation-style verifier and register it in one call.

    Sibling of :func:`probe.register_probe_verifier`. Replaces the
    three-layer ``register_verifier("X")(build_validation_verifier(...))``
    idiom with one self-contained registration call.
    """
    return register_verifier(service)(
        build_validation_verifier(
            service, build_config=build_config, validate_config=validate_config
        )
    )
