"""Tests for .github/ci/check_direct_imports.py."""

from __future__ import annotations

import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parents[2] / ".github" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_direct_imports import (  # noqa: E402
    _NESTED_BASELINE_IGNORES,
    find_direct_violations,
    find_nested_direct_violations,
)
from check_import_cycles import _nested_imports, discover_first_party_roots  # noqa: E402


def test_find_direct_violations_flags_new_edge() -> None:
    # All edges below are forbidden by ``_FORBIDDEN_DIRECT``:
    # - ``integrations`` cannot import from ``tools``
    # - ``platform`` cannot import from ``surfaces``
    # - ``tools`` cannot import from ``surfaces``
    graph = {
        "integrations.grafana.tools": {"tools.tool_decorator"},
        "tools.fleet_monitoring": {"surfaces.cli.commands.doctor"},
        "platform.analytics.provider": {"surfaces.cli.wizard.store"},
    }
    violations = find_direct_violations(graph, baseline_ignores=frozenset())
    edges = {v.edge for v in violations}
    assert "integrations.grafana.tools -> tools.tool_decorator" in edges
    assert "tools.fleet_monitoring -> surfaces.cli.commands.doctor" in edges
    assert "platform.analytics.provider -> surfaces.cli.wizard.store" in edges


def test_find_direct_violations_respects_baseline() -> None:
    graph = {
        "tools.fleet_monitoring": {"surfaces.cli.commands.doctor"},
    }
    violations = find_direct_violations(
        graph,
        baseline_ignores=frozenset({"tools.fleet_monitoring -> surfaces.cli.commands.doctor"}),
    )
    assert violations == []


def test_nested_imports_ignores_module_top_level() -> None:
    source = "from surfaces.cli import doctor\n\ndef f():\n    from core.llm import client\n"
    nested = _nested_imports(source, first_party_roots=frozenset({"surfaces", "core", "tools"}))
    assert nested == [("core.llm", 4)]


def test_nested_imports_flags_function_and_class_bodies() -> None:
    source = (
        "class C:\n"
        "    def m(self):\n"
        "        from surfaces.cli.wizard import store\n"
        "def f():\n"
        "    from surfaces.interactive_shell.ui import DIM\n"
    )
    nested = _nested_imports(
        source,
        first_party_roots=frozenset({"surfaces", "tools"}),
    )
    assert ("surfaces.cli.wizard", 3) in nested
    assert ("surfaces.interactive_shell.ui", 5) in nested


def test_nested_imports_flags_imports_inside_for_while_and_match() -> None:
    source = (
        "def f():\n"
        "    for _ in [1]:\n"
        "        from surfaces.cli import doctor\n"
        "    while False:\n"
        "        from surfaces.cli.wizard import store\n"
        "    match 0:\n"
        "        case _:\n"
        "            from surfaces.interactive_shell.ui import DIM\n"
    )
    nested = _nested_imports(
        source,
        first_party_roots=frozenset({"surfaces", "tools"}),
    )
    assert ("surfaces.cli", 3) in nested
    assert ("surfaces.cli.wizard", 5) in nested
    assert ("surfaces.interactive_shell.ui", 8) in nested


def test_find_nested_direct_violations_flags_new_edge(tmp_path: Path) -> None:
    module_dir = tmp_path / "tools" / "fleet_monitoring"
    module_dir.mkdir(parents=True)
    module_dir.joinpath("__init__.py").write_text(
        "def run():\n    from surfaces.cli.commands import doctor\n",
        encoding="utf-8",
    )
    violations = find_nested_direct_violations(
        tmp_path,
        ("tools", "surfaces", "core"),
        baseline_ignores=frozenset(),
    )
    assert len(violations) == 1
    assert violations[0].edge == "tools.fleet_monitoring -> surfaces.cli.commands"
    assert violations[0].lineno == 2


def test_find_nested_direct_violations_respects_baseline(tmp_path: Path) -> None:
    module_dir = tmp_path / "tools" / "fleet_monitoring"
    module_dir.mkdir(parents=True)
    module_dir.joinpath("__init__.py").write_text(
        "def run():\n    from surfaces.cli.commands import doctor\n",
        encoding="utf-8",
    )
    violations = find_nested_direct_violations(
        tmp_path,
        ("tools", "surfaces", "core"),
        baseline_ignores=frozenset(
            {"tools.fleet_monitoring -> surfaces.cli.commands"},
        ),
    )
    assert violations == []


def test_find_nested_direct_violations_ignores_legal_nested_imports(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools" / "helper"
    tools_dir.mkdir(parents=True)
    tools_dir.joinpath("__init__.py").write_text(
        "def run():\n    from core.llm import client\n",
        encoding="utf-8",
    )
    core_dir = tmp_path / "core" / "helper"
    core_dir.mkdir(parents=True)
    core_dir.joinpath("__init__.py").write_text(
        "def run():\n    from platform.common import task_types\n",
        encoding="utf-8",
    )
    violations = find_nested_direct_violations(
        tmp_path,
        ("tools", "core", "platform", "surfaces"),
        baseline_ignores=frozenset(),
    )
    assert violations == []


def test_repo_has_no_unbaselined_nested_direct_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    first_party_roots = discover_first_party_roots(root)
    violations = find_nested_direct_violations(root, first_party_roots)
    assert violations == [], (
        "Unexpected nested direct import violations — update _NESTED_BASELINE_IGNORES "
        "only with linked burn-down issues:\n"
        + "\n".join(f"  {v.edge} (line {v.lineno})" for v in violations)
    )
    assert len(_NESTED_BASELINE_IGNORES) == 7
