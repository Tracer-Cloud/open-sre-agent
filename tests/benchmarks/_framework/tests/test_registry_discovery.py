"""Tests for filesystem-based adapter auto-discovery.

Phase 5 of the framework decoupling replaced the hardcoded
``_KNOWN_ADAPTER_MODULES`` tuple in ``registry.py`` with a walk of
``tests/benchmarks/*/adapter.py``. These tests pin the discovery
contract so a future regression cannot silently re-introduce
adapter-name hardcoding.

The walk rules under test:

  1. A subdirectory of ``tests/benchmarks/`` is recognised as an
     adapter ONLY if it contains an ``adapter.py`` file.
  2. Directories whose name starts with ``_`` or ``.`` are skipped
     (``_framework``, ``__pycache__``, etc.).
  3. Discovery is deterministic — the returned tuple is sorted so the
     bootstrap order is stable across runs.
  4. Adding a new adapter under ``tests/benchmarks/<name>/adapter.py``
     requires zero framework edits — the walk picks it up automatically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks._framework.registry import _discover_adapter_modules

# --------------------------------------------------------------------------- #
# Live-tree discovery — exercises the actual benchmark directory               #
# --------------------------------------------------------------------------- #


def test_discovery_finds_cloudopsbench() -> None:
    """The repository has exactly one shipped adapter today
    (``cloudopsbench``). The walk must find it on the real filesystem
    so the registry's bootstrap path is exercised end-to-end."""
    modules = _discover_adapter_modules()
    assert "tests.benchmarks.cloudopsbench.adapter" in modules


def test_discovery_skips_framework_directory() -> None:
    """``_framework/`` is the framework itself, not an adapter. The
    walk must skip it via the underscore-prefix rule even though it
    sits next to adapter packages."""
    modules = _discover_adapter_modules()
    assert not any("_framework" in m for m in modules), (
        f"_framework leaked into adapter discovery: {modules}"
    )


def test_discovery_skips_directories_without_adapter_py() -> None:
    """``interactive_shell/`` exists under ``tests/benchmarks/`` but
    has no ``adapter.py`` — it is a non-adapter utility package. The
    walk must skip it so the registry does not try to import a module
    that has no ``register_adapter()`` call."""
    modules = _discover_adapter_modules()
    assert not any("interactive_shell" in m for m in modules), (
        f"non-adapter directory leaked into discovery: {modules}"
    )


def test_discovery_returns_sorted_paths() -> None:
    """Bootstrap order must be deterministic so two processes that
    walk the same tree register adapters in the same order. The walk
    sorts its output."""
    modules = _discover_adapter_modules()
    assert list(modules) == sorted(modules)


# --------------------------------------------------------------------------- #
# Isolated-tree discovery — pin the walk's structural rules                   #
# --------------------------------------------------------------------------- #


def _make_fake_benchmarks_tree(root: Path, layout: dict[str, bool]) -> Path:
    """Build a ``tests/benchmarks``-shaped tree under ``root``.

    ``layout`` maps subdirectory names to whether the directory should
    contain an ``adapter.py``. Returns the path to the synthesized
    ``tests/benchmarks/_framework`` directory so the walk has a clear
    anchor for its parent computation.
    """
    benchmarks = root / "tests" / "benchmarks"
    benchmarks.mkdir(parents=True)
    for name, has_adapter in layout.items():
        sub = benchmarks / name
        sub.mkdir()
        if has_adapter:
            (sub / "adapter.py").write_text("")
    framework = benchmarks / "_framework"
    framework.mkdir(exist_ok=True)
    return framework


def test_isolated_walk_skips_underscore_dirs_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the walk against a synthetic tree to pin the rules in
    isolation. Underscore-prefixed dirs are skipped even when they
    contain an ``adapter.py``; dirs WITHOUT an ``adapter.py`` are
    skipped even when they are otherwise well-named."""
    framework = _make_fake_benchmarks_tree(
        tmp_path,
        {
            "good_adapter": True,  # has adapter.py → included
            "_internal_adapter": True,  # underscore prefix → skipped
            "stub_dir": False,  # no adapter.py → skipped
            "another_good": True,  # has adapter.py → included
        },
    )
    # The walk computes ``benchmarks_dir`` from its own file's parent;
    # patch ``__file__`` so the test exercises the real walk logic with
    # a controlled tree.
    monkeypatch.setattr(
        "tests.benchmarks._framework.registry.__file__",
        str(framework / "registry.py"),
    )
    from tests.benchmarks._framework.registry import _discover_adapter_modules

    modules = _discover_adapter_modules()
    assert modules == (
        "tests.benchmarks.another_good.adapter",
        "tests.benchmarks.good_adapter.adapter",
    )


def test_isolated_walk_returns_empty_tuple_when_no_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean tree with no adapter packages returns an empty tuple
    rather than raising. The bootstrap path tolerates this — a
    no-adapter framework is valid (e.g. a downstream consumer using
    only ``_framework/`` utilities)."""
    framework = _make_fake_benchmarks_tree(
        tmp_path,
        {"_framework_helper": True, "no_adapter_here": False},
    )
    monkeypatch.setattr(
        "tests.benchmarks._framework.registry.__file__",
        str(framework / "registry.py"),
    )
    from tests.benchmarks._framework.registry import _discover_adapter_modules

    assert _discover_adapter_modules() == ()
