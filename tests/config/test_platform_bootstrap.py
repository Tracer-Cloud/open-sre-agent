"""Process-wide first-party ``platform`` heal + import guard.

Poison / stdlib-recache scenarios must run in a fresh interpreter. Evicting
``platform.*`` from ``sys.modules`` and reloading leaves earlier
``from platform… import`` bindings pointing at dead modules; dotted
``monkeypatch.setattr`` then patches the new module and misses the bound
callables — which showed up as empty scheduler credential stubs in CI.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from config import platform_bootstrap as boot


@pytest.fixture(autouse=True)
def _restore_import_guard() -> None:
    """Keep the guard hooked; never evict ``platform.*`` (orphans other test imports)."""
    yield
    boot.reset_platform_import_guard_for_tests()
    boot.ensure_project_platform_package()


def _run_in_fresh_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parents[2]),
    )


def test_ensure_loads_project_package_and_activates_guard() -> None:
    boot.reset_platform_import_guard_for_tests()
    boot.ensure_project_platform_package()

    assert hasattr(sys.modules["platform"], "__path__")
    assert boot.platform_import_guard_is_active()


def test_ensure_is_safe_to_call_twice() -> None:
    boot.ensure_project_platform_package()
    boot.ensure_project_platform_package()
    assert boot.platform_import_guard_is_active()


def test_from_import_stays_bound_to_sys_modules_credentials() -> None:
    """Canary: in-process eviction must not leave orphaned ``platform.*`` imports."""
    from platform.scheduling.scheduler.credentials import resolve_telegram_credentials

    live = sys.modules["platform.scheduling.scheduler.credentials"]
    assert resolve_telegram_credentials.__globals__ is live.__dict__
    assert resolve_telegram_credentials is live.resolve_telegram_credentials


def test_guard_heals_on_import_module_after_recache() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        import importlib, sys, types
        ensure_project_platform_package()
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        importlib.import_module("platform.errors")
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_guard_heals_on_from_import_after_recache() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        import sys, types
        ensure_project_platform_package()
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        from platform.errors import OpenSREError
        assert OpenSREError is not None
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_guard_heals_real_stdlib_module_poison() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        import importlib.util, sys, sysconfig, types
        from pathlib import Path
        ensure_project_platform_package()
        path = Path(sysconfig.get_path("stdlib")) / "platform.py"
        spec = importlib.util.spec_from_file_location("_stdlib", path)
        stdlib = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stdlib)
        assert not hasattr(stdlib, "__path__")
        sys.modules["platform"] = stdlib
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        import platform as project_platform
        from platform.errors import OpenSREError
        assert OpenSREError is not None
        assert hasattr(project_platform, "__path__")
        assert project_platform.system()
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_refresh_runtime_metadata_after_platform_recache() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        import sys, types
        ensure_project_platform_package()
        from surfaces.interactive_shell.runtime.context import create_repl_runtime_context
        session = create_repl_runtime_context().session
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        session.refresh_runtime_metadata()
        assert hasattr(sys.modules["platform"], "__path__")
        assert "capability_warnings" in session.runtime_metadata
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
