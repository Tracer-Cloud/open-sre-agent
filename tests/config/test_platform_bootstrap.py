"""Process-wide first-party ``platform`` heal + import guard."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import sysconfig
import types
from pathlib import Path

import pytest

from config import platform_bootstrap as boot


def _poison_platform_cache(replacement: types.ModuleType) -> None:
    """Replace ``sys.modules['platform']`` with a non-package and drop children."""
    sys.modules["platform"] = replacement
    for name in list(sys.modules):
        if name.startswith("platform."):
            del sys.modules[name]


def _load_real_stdlib_platform() -> types.ModuleType:
    path = Path(sysconfig.get_path("stdlib")) / "platform.py"
    spec = importlib.util.spec_from_file_location("_opensre_test_stdlib_platform", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not hasattr(module, "__path__")
    return module


@pytest.fixture(autouse=True)
def _restore_platform_after_each_test() -> None:
    """Poison tests must not leave a broken ``platform`` cache for later suites."""
    yield
    boot.reset_platform_import_guard_for_tests()
    for name in list(sys.modules):
        if name == "platform" or name.startswith("platform."):
            del sys.modules[name]
    boot.ensure_project_platform_package()


def test_ensure_loads_project_package_and_activates_guard() -> None:
    boot.reset_platform_import_guard_for_tests()
    _poison_platform_cache(types.ModuleType("platform"))

    boot.ensure_project_platform_package()

    assert hasattr(sys.modules["platform"], "__path__")
    assert boot.platform_import_guard_is_active()


def test_guard_heals_on_import_module_after_recache() -> None:
    boot.ensure_project_platform_package()
    _poison_platform_cache(types.ModuleType("platform"))

    importlib.import_module("platform.errors")

    assert hasattr(sys.modules["platform"], "__path__")
    assert "platform.errors" in sys.modules


def test_guard_heals_on_from_import_after_recache() -> None:
    boot.ensure_project_platform_package()
    _poison_platform_cache(types.ModuleType("platform"))

    from platform.errors import OpenSREError

    assert OpenSREError is not None
    assert hasattr(sys.modules["platform"], "__path__")


def test_ensure_is_safe_to_call_twice() -> None:
    boot.ensure_project_platform_package()
    boot.ensure_project_platform_package()

    _poison_platform_cache(types.ModuleType("platform"))
    importlib.import_module("platform.errors")

    assert hasattr(sys.modules["platform"], "__path__")
    assert boot.platform_import_guard_is_active()


def test_guard_heals_real_stdlib_module_poison() -> None:
    boot.ensure_project_platform_package()
    _poison_platform_cache(_load_real_stdlib_platform())

    import platform as project_platform
    from platform.errors import OpenSREError

    assert OpenSREError is not None
    assert hasattr(project_platform, "__path__")
    assert project_platform.system()


def test_refresh_runtime_metadata_after_platform_recache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late ``from platform…`` in session code must heal via the guard alone."""
    from surfaces.interactive_shell.runtime.context import create_repl_runtime_context

    boot.ensure_project_platform_package()
    session = create_repl_runtime_context().session
    monkeypatch.setitem(sys.modules, "platform", types.ModuleType("platform"))
    for name in list(sys.modules):
        if name.startswith("platform."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    session.refresh_runtime_metadata()

    assert hasattr(sys.modules["platform"], "__path__")
    assert "capability_warnings" in session.runtime_metadata
