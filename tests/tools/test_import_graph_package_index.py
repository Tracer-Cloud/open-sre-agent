"""Tests for JVM package index resolution."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners.import_graph.graph import build_import_graph
from tools.architecture_issue_tool.scanners.import_graph.package_index import (
    build_package_index,
    resolve_jvm_import,
)
from tools.architecture_issue_tool.scanners.import_graph.resolve import unit_for_file

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "java_maven_repo"
)


def test_unit_for_file_uses_path_before_src_main_java() -> None:
    java_file = (
        _FIXTURE_ROOT
        / "clients"
        / "src"
        / "main"
        / "java"
        / "com"
        / "example"
        / "clients"
        / "Client.java"
    )

    assert unit_for_file(_FIXTURE_ROOT, java_file) == "clients"


def test_build_package_index_maps_packages_to_modules() -> None:
    index = build_package_index(_FIXTURE_ROOT)

    assert index["com.example.clients"] == "clients"
    assert index["com.example.core.util"] == "core"


def test_resolve_jvm_import_matches_longest_package_prefix() -> None:
    index = build_package_index(_FIXTURE_ROOT)

    assert resolve_jvm_import(index, "com.example.core.util.Helper") == "core"


def test_build_import_graph_resolves_java_cross_module_edges() -> None:
    graph, raw_count, resolved_count = build_import_graph(_FIXTURE_ROOT)

    assert raw_count > 0
    assert resolved_count > 0
    assert any(edge.source_unit == "clients" and edge.target_unit == "core" for edge in graph.edges)
