"""Process-wide first-party ``platform`` heal + import guard."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from config import platform_bootstrap as boot


def _force_reload_project_platform() -> None:
    """Drop every ``platform`` cache entry and re-heal (avoids parent/child split-brain)."""
    for name in list(sys.modules):
        if name == "platform" or name.startswith("platform."):
            del sys.modules[name]
    boot.ensure_project_platform_package()


@pytest.fixture(autouse=True)
def _restore_guard() -> None:
    """Keep guard / heal state from leaking across tests in this module."""
    yield
    boot.reset_platform_import_guard_for_tests()
    _force_reload_project_platform()


def test_ensure_installs_project_package_and_guard() -> None:
    boot.reset_platform_import_guard_for_tests()
    sys.modules["platform"] = types.ModuleType("platform")

    boot.ensure_project_platform_package()

    assert hasattr(sys.modules["platform"], "__path__")
    assert getattr(sys, boot._GUARD_SYS_ATTR, False) is True


def test_guard_heals_on_import_after_recache() -> None:
    boot.ensure_project_platform_package()
    sys.modules["platform"] = types.ModuleType("platform")
    for name in list(sys.modules):
        if name.startswith("platform."):
            del sys.modules[name]

    importlib.import_module("platform.errors")

    assert hasattr(sys.modules["platform"], "__path__")
    assert "platform.errors" in sys.modules


def test_guard_heals_on_from_import_after_recache() -> None:
    boot.ensure_project_platform_package()
    sys.modules["platform"] = types.ModuleType("platform")
    for name in list(sys.modules):
        if name.startswith("platform."):
            del sys.modules[name]

    # builtins.__import__ path used by ``from platform… import …``
    from platform.errors import OpenSREError

    assert OpenSREError is not None
    assert hasattr(sys.modules["platform"], "__path__")
