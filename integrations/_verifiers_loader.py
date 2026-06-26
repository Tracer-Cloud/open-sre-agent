"""Auto-discover and import every per-vendor verifier module so the
``@register_verifier`` decorators fire at import time.

Two locations are scanned:

* ``integrations.verifiers.*`` — config-only integrations.
* ``vendors.<vendor>.verifier`` — integrations with a dedicated
  vendor SDK client package.

Adding a new vendor is one new file in either location. No edits to a
central import list are required — this loader walks both trees.

Public surface: :func:`register_all_verifiers`. Callers invoke it once
during startup (``integrations.verify`` and the test suite both do).
Re-invocation is safe: the registry's ``register_verifier`` decorator
replaces existing entries silently.
"""

from __future__ import annotations

import importlib
import pkgutil

import integrations.verifiers as _verifiers_pkg
import vendors as _vendors_pkg

_VERIFIER_SUBMODULE = "verifier"


def _load_integrations_verifiers() -> None:
    """Import every ``integrations.verifiers.<service>`` module."""
    for module_info in pkgutil.iter_modules(_verifiers_pkg.__path__):
        importlib.import_module(f"{_verifiers_pkg.__name__}.{module_info.name}")


def _load_vendor_verifiers() -> None:
    """Import every ``vendors.<vendor>.verifier`` module that exists.

    Iterates the ``vendors`` package one level deep, only attempting
    ``<vendor>.verifier`` when ``<vendor>`` is itself a package. A
    ``ModuleNotFoundError`` for the ``verifier`` submodule is silently
    skipped — many vendor packages have no verifier.
    """
    for module_info in pkgutil.iter_modules(_vendors_pkg.__path__):
        if not module_info.ispkg:
            continue
        candidate = f"{_vendors_pkg.__name__}.{module_info.name}.{_VERIFIER_SUBMODULE}"
        try:
            importlib.import_module(candidate)
        except ModuleNotFoundError as err:
            # Distinguish "no verifier.py here" (expected) from "verifier.py
            # exists but its own imports failed" (a real error we must surface).
            if err.name != candidate:
                raise


def register_all_verifiers() -> None:
    """Import every vendor verifier module so its ``@register_verifier``
    decorator fires. Idempotent.
    """
    _load_integrations_verifiers()
    _load_vendor_verifiers()
