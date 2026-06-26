"""Plugin registry: integration-local verifiers register themselves by service name.

Each integration verifier module decorates its verify function with
``@register_verifier("<service>")`` from ``integrations/<service>/verifier.py``.
The registry is a simple module-level dict; lookup is
``get_verifier("<service>")``.

Auto-discovery: the loader at
``integrations/_verifiers_loader.py`` walks integration packages via
``pkgutil`` and triggers each module's ``@register_verifier`` decorator on
import. New integration verifier file = registered automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

VerifierFn = Callable[[str, dict[str, Any]], dict[str, str]]

_REGISTRY: dict[str, VerifierFn] = {}


def register_verifier(service: str) -> Callable[[VerifierFn], VerifierFn]:
    """Decorator: register ``fn`` as the verifier for ``service``.

    Replaces a pre-existing entry silently — re-importing a verifier
    module (e.g. in tests that reload modules) should be safe rather
    than blowing up.

    Returns the decorated function unchanged, so call sites can keep
    the registered callable as a module-level name if they want.
    """

    def _decorator(fn: VerifierFn) -> VerifierFn:
        _REGISTRY[service] = fn
        return fn

    return _decorator


def get_verifier(service: str) -> VerifierFn | None:
    """Return the verifier registered for ``service``, or ``None``."""
    return _REGISTRY.get(service)


def list_verifiers() -> list[str]:
    """Return the sorted list of registered service names.

    Used by tests + the verify CLI to enumerate what's available.
    """
    return sorted(_REGISTRY)


def _snapshot_for_testing() -> dict[str, VerifierFn]:
    """Return a shallow copy of the registry. Tests pair with restore."""
    return dict(_REGISTRY)


def _restore_for_testing(snapshot: dict[str, VerifierFn]) -> None:
    """Replace registry contents with ``snapshot``. Pairs with snapshot."""
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def _reset_for_testing() -> None:
    """Drop every registration. Tests use this for a known-empty start.

    Production code never calls this — it would unregister every
    verifier the loader imported.
    """
    _REGISTRY.clear()
