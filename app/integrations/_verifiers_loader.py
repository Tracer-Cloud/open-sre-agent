"""Auto-discover and import every per-vendor verifier module so the
``@register_verifier`` decorators fire at import time.

Two locations are scanned:

* ``app.integrations.verifiers.*`` — config-only integrations.
* ``app.services.<vendor>.verifier`` — integrations with a dedicated
  vendor SDK client package.

Adding a new vendor is one new file in either location. No edits to a
central import list are required — this loader walks both trees.
"""

from __future__ import annotations

import importlib
import pkgutil

import app.integrations.verifiers as _verifiers_pkg
import app.services as _services_pkg

_VERIFIER_SUBMODULE = "verifier"


def _load_integrations_verifiers() -> None:
    """Import every ``app.integrations.verifiers.<service>`` module."""
    for module_info in pkgutil.iter_modules(_verifiers_pkg.__path__):
        importlib.import_module(f"{_verifiers_pkg.__name__}.{module_info.name}")


def _load_service_verifiers() -> None:
    """Import every ``app.services.<vendor>.verifier`` module that exists.

    Iterates the ``app.services`` package one level deep, only attempting
    ``<vendor>.verifier`` when ``<vendor>`` is itself a package. A
    ``ModuleNotFoundError`` for the ``verifier`` submodule is silently
    skipped — many service packages have no verifier.
    """
    for module_info in pkgutil.iter_modules(_services_pkg.__path__):
        if not module_info.ispkg:
            continue
        candidate = f"{_services_pkg.__name__}.{module_info.name}.{_VERIFIER_SUBMODULE}"
        try:
            importlib.import_module(candidate)
        except ModuleNotFoundError as err:
            # Distinguish "no verifier.py here" (expected) from "verifier.py
            # exists but its own imports failed" (a real error we must surface).
            if err.name != candidate:
                raise


_load_integrations_verifiers()
_load_service_verifiers()
