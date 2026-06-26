"""Auto-discover and import every per-integration verifier module so the
``@register_verifier`` decorators fire at import time.

Verifier modules live next to the integration they verify:
``integrations.<name>.verifier``.

Adding a new verifier is one new file in the owning integration package. No
edits to a central import list are required — this loader walks the integration
tree.

Public surface: :func:`register_all_verifiers`. Callers invoke it once
during startup (``integrations.verify`` and the test suite both do).
Re-invocation is safe: the registry's ``register_verifier`` decorator
replaces existing entries silently.
"""

from __future__ import annotations

import importlib
import pkgutil

import integrations as _integrations_pkg

_VERIFIER_SUBMODULE = "verifier"
_SKIP_INTEGRATION_PACKAGES = frozenset({"verification", "__pycache__"})


def _load_integration_local_verifiers() -> None:
    """Import every ``integrations.<name>.verifier`` module that exists.

    Iterates ``integrations`` one level deep and attempts a verifier import only
    for package integrations. A ``ModuleNotFoundError`` for the verifier
    submodule is skipped; import failures inside an existing verifier still
    surface.
    """
    for module_info in pkgutil.iter_modules(_integrations_pkg.__path__):
        if not module_info.ispkg or module_info.name in _SKIP_INTEGRATION_PACKAGES:
            continue
        candidate = f"{_integrations_pkg.__name__}.{module_info.name}.{_VERIFIER_SUBMODULE}"
        try:
            importlib.import_module(candidate)
        except ModuleNotFoundError as err:
            # Distinguish "no verifier.py here" (expected) from "verifier.py
            # exists but its own imports failed" (a real error we must surface).
            if err.name != candidate:
                raise


def register_all_verifiers() -> None:
    """Import every integration verifier module so its ``@register_verifier``
    decorator fires. Idempotent.
    """
    _load_integration_local_verifiers()
