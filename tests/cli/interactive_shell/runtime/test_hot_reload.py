from __future__ import annotations

import importlib
import io
import os
import sys
from pathlib import Path

import pytest
from rich.console import Console

from app.cli.interactive_shell.runtime.hot_reload import (
    HotReloadCoordinator,
    _default_watch_root,
)


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, highlight=False), buffer


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    next_ns = stat.st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(next_ns, next_ns))


def test_hot_reload_reloads_changed_loaded_module(tmp_path: Path) -> None:
    package_dir = tmp_path / "demoapp"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    module_path = package_dir / "feature.py"
    module_path.write_text("VALUE = 1\n", encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("demoapp.feature")
        assert module.VALUE == 1

        coordinator = HotReloadCoordinator(
            watch_root=package_dir,
            package_prefix="demoapp",
            dependent_modules=(),
        )
        module_path.write_text("VALUE = 2\n", encoding="utf-8")
        _bump_mtime(module_path)
        importlib.invalidate_caches()

        console, _ = _console()
        result = coordinator.check_and_reload(console)

        assert result is not None
        assert result.reloaded_modules == ("demoapp.feature",)
        assert module.VALUE == 2
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("demoapp.feature", None)
        sys.modules.pop("demoapp", None)


def test_hot_reload_returns_none_when_unchanged(tmp_path: Path) -> None:
    package_dir = tmp_path / "demoapp"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")

    coordinator = HotReloadCoordinator(
        watch_root=package_dir,
        package_prefix="demoapp",
        dependent_modules=(),
    )
    console, _ = _console()

    assert coordinator.check_and_reload(console) is None


def test_hot_reload_reloads_changed_modules_before_dependents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_dir = tmp_path / "demoapp"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    changed_path = package_dir / "action_executor.py"
    changed_path.write_text("VALUE = 1\n", encoding="utf-8")
    (package_dir / "agent_actions.py").write_text("VALUE = 1\n", encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module("demoapp.action_executor")
        importlib.import_module("demoapp.agent_actions")
        coordinator = HotReloadCoordinator(
            watch_root=package_dir,
            package_prefix="demoapp",
            dependent_modules=("demoapp.agent_actions",),
        )
        changed_path.write_text("VALUE = 2\n", encoding="utf-8")
        _bump_mtime(changed_path)
        reload_order: list[str] = []

        def _fake_reload(module: object) -> object:
            assert hasattr(module, "__name__")
            reload_order.append(module.__name__)
            return module

        monkeypatch.setattr(
            "app.cli.interactive_shell.runtime.hot_reload.importlib.reload", _fake_reload
        )

        console, _ = _console()
        result = coordinator.check_and_reload(console)

        assert result is not None
        assert reload_order == ["demoapp.action_executor", "demoapp.agent_actions"]
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("demoapp.action_executor", None)
        sys.modules.pop("demoapp.agent_actions", None)
        sys.modules.pop("demoapp", None)


def test_hot_reload_reports_reload_errors(tmp_path: Path) -> None:
    package_dir = tmp_path / "demoapp"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    module_path = package_dir / "broken.py"
    module_path.write_text("VALUE = 1\n", encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module("demoapp.broken")
        coordinator = HotReloadCoordinator(
            watch_root=package_dir,
            package_prefix="demoapp",
            dependent_modules=(),
        )
        module_path.write_text("def nope(:\n", encoding="utf-8")
        _bump_mtime(module_path)
        importlib.invalidate_caches()

        console, buffer = _console()
        result = coordinator.check_and_reload(console)

        assert result is not None
        assert result.errors
        assert "continuing with the last usable code" in buffer.getvalue()
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("demoapp.broken", None)
        sys.modules.pop("demoapp", None)


def test_default_watch_root_points_at_app_directory() -> None:
    """Regression: the implicit ``HotReloadCoordinator()`` watch_root must exist.

    Before the ``runtime/`` subpackage move the default was
    ``Path(__file__).parents[3] / "app"``, which after the move silently
    resolved to ``<repo>/app/app`` — a non-existent path. ``_scan()`` then
    returned an empty snapshot and ``check_and_reload()`` always reported
    "no changes", so hot reload looked wired up but never fired in
    production. This test pins the corrected ``parents[4]`` so the same
    bug can't reappear if the module ever moves again.
    """
    root = _default_watch_root()
    assert root.is_dir(), f"default watch_root {root} should resolve to the live app/ tree"
    assert root.name == "app", f"default watch_root should be the app/ directory, got {root}"
    # The coordinator instantiated with no kwargs should also pick the same
    # directory and not crash on ``_scan()``.
    coordinator = HotReloadCoordinator()
    assert coordinator.watch_root == root.resolve()
