"""REPL entrypoints re-establish the first-party ``platform`` package.

An embedding host can import the shell (running the module-level bootstrap),
then re-cache stdlib ``platform`` in ``sys.modules``, then call an entrypoint.
``run_repl*`` calls ``ensure_project_platform_package`` (heal + import guard);
later ``platform.*`` imports self-heal through the guard.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest
from rich.console import Console

import surfaces.interactive_shell.main as main
from config.platform_bootstrap import ensure_project_platform_package
from config.repl_config import ReplConfig


class _Reached(Exception):
    """Raised from a patched seam once the platform imports have succeeded."""


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Contain the real ``install_shell_log_handler`` side effect."""
    root = logging.getLogger()
    saved = root.handlers[:]
    yield
    root.handlers[:] = saved


@pytest.fixture(autouse=True)
def _reload_platform_after_shadow() -> Iterator[None]:
    """Shadow tests can leave parent/child ``platform`` split-brain for later tests."""
    yield
    for name in list(sys.modules):
        if name == "platform" or name.startswith("platform."):
            del sys.modules[name]
    ensure_project_platform_package()


def _shadow_stdlib_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a host that re-cached stdlib ``platform`` after the shell loaded."""
    stub = types.ModuleType("platform")  # a plain module has no __path__ → not a package
    monkeypatch.setitem(sys.modules, "platform", stub)
    for name in list(sys.modules):
        if name.startswith("platform."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    assert not hasattr(sys.modules["platform"], "__path__")


def test_run_repl_async_bootstraps_before_platform_analytics_import(
    monkeypatch: pytest.MonkeyPatch, restore_logging: None
) -> None:
    # Arrange: package available, load config, then a host shadows the package.
    ensure_project_platform_package()
    cfg = ReplConfig.load()
    _shadow_stdlib_platform(monkeypatch)

    monkeypatch.setattr(main, "build_prompt_session", object)

    def _raise_reached(**_kwargs: Any) -> None:
        raise _Reached

    monkeypatch.setattr(main, "create_repl_runtime_context", _raise_reached)

    # Act / Assert: reaching the patched seam proves the `platform.analytics` /
    # `platform.logging` imports succeeded. Without the ensure, a
    # ModuleNotFoundError would escape here instead of _Reached.
    with pytest.raises(_Reached):
        asyncio.run(main.run_repl_async(config=cfg, console=Console(force_terminal=False)))
    assert hasattr(sys.modules["platform"], "__path__")


def test_run_repl_bootstraps_platform_before_any_work(
    monkeypatch: pytest.MonkeyPatch, restore_logging: None
) -> None:
    # Arrange
    ensure_project_platform_package()
    cfg = ReplConfig.load()
    _shadow_stdlib_platform(monkeypatch)

    # Act: non-tty stdin with no initial input returns 0 early — but only after
    # the entrypoint's ensure has re-established the package.
    rc = main.run_repl(config=cfg, console=Console(force_terminal=False))

    # Assert
    assert rc == 0
    assert hasattr(sys.modules["platform"], "__path__")


def test_submodule_import_after_platform_recache_survives() -> None:
    """Import guard heals ``platform.*`` after a mid-process stdlib re-cache.

    Composition roots call ``ensure`` once (installs the guard). Leaves import
    ``platform.*`` normally; a fresh interpreter pins first-import order.
    """
    import subprocess

    code = (
        "from config.platform_bootstrap import ensure_project_platform_package\n"
        "ensure_project_platform_package()\n"
        "import surfaces.interactive_shell.runtime\n"
        "import sys, types\n"
        "sys.modules['platform'] = types.ModuleType('platform')\n"
        "[sys.modules.pop(n) for n in list(sys.modules) if n.startswith('platform.')]\n"
        "import surfaces.interactive_shell.runtime.turn_host as th\n"
        "assert hasattr(th, 'run_agent_turn')\n"
        "assert hasattr(sys.modules['platform'], '__path__')\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
