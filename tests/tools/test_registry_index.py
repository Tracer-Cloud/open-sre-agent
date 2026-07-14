"""Contract: the static descriptor index matches the imported registry (#3686).

The index (AST scan + pinned fallback descriptors) must equal the imported
registry exactly — same tool names, same surfaces, same source — so surface-
scoped loads built on it can never diverge from the eager snapshot. If this
drifts, a tool changed shape: make its metadata literal, or update the pinned
``_fallback_descriptors`` in ``tools/registry_index.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import tools.registry as registry_module
from tools.registry_index import build_descriptor_index


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    # Assert against the on-disk tool set: drop any tools other tests left in the
    # global ``_external_tool_packages`` (the AST index only sees disk).
    saved = list(registry_module._external_tool_packages)
    registry_module._external_tool_packages.clear()
    registry_module.clear_tool_registry_cache()
    yield
    registry_module._external_tool_packages[:] = saved
    registry_module.clear_tool_registry_cache()


def _registered_by_name() -> dict[str, Any]:
    return {tool.name: tool for tool in registry_module.get_registered_tools()}


def test_index_tool_set_matches_registry_exactly() -> None:
    index = set(build_descriptor_index())
    registered = set(_registered_by_name())
    assert index == registered, {
        "missing_from_index": sorted(registered - index),
        "not_registered": sorted(index - registered),
    }


def test_descriptor_surfaces_match_registry() -> None:
    index = build_descriptor_index()
    registered = _registered_by_name()
    mismatched = {
        name: (descriptor.surfaces, tuple(getattr(registered[name], "surfaces", ()) or ()))
        for name, descriptor in index.items()
        if set(descriptor.surfaces) != set(getattr(registered[name], "surfaces", ()) or ())
    }
    assert mismatched == {}


def test_descriptor_source_matches_registry_when_known() -> None:
    index = build_descriptor_index()
    registered = _registered_by_name()
    for name, descriptor in index.items():
        if descriptor.source is None:
            continue
        assert descriptor.source == getattr(registered[name], "source", None), name


def test_descriptor_module_is_dotted_import_path() -> None:
    index = build_descriptor_index()
    assert index["query_datadog_all"].module == "integrations.datadog.tools"
    assert index["shell_run"].module == "tools.interactive_shell.actions.shell"


def test_surface_scoped_load_equals_full_filtered() -> None:
    """The fast surface path must return exactly the full snapshot filtered by surface.

    Compares by ``(name, origin_module)`` so a duplicate tool name that resolves to
    a different module in the surface path (alphabetical) than the full path
    (package-declaration order) fails here rather than silently diverging.
    """
    full = registry_module.get_registered_tools()
    for surface in ("action", "chat", "investigation"):
        scoped = {
            (tool.name, tool.origin_module)
            for tool in registry_module.get_registered_tools(surface)
        }
        expected = {
            (tool.name, tool.origin_module)
            for tool in full
            if surface in (getattr(tool, "surfaces", ()) or ())
        }
        assert scoped == expected, surface


def test_get_tool_descriptors_match_surface_load() -> None:
    assert {d.name for d in registry_module.get_tool_descriptors()} == set(build_descriptor_index())
    for surface in ("action", "chat", "investigation"):
        descriptor_names = {d.name for d in registry_module.get_tool_descriptors(surface)}
        tool_names = {tool.name for tool in registry_module.get_registered_tools(surface)}
        assert descriptor_names == tool_names, surface


def test_load_tool_materializes_the_executor() -> None:
    # @tool-decorated (AST-indexed) and RegisteredTool-constructed (pinned) both load.
    for name in ("query_datadog_all", "shell_run"):
        descriptor = next(d for d in registry_module.get_tool_descriptors() if d.name == name)
        tool = registry_module.load_tool(descriptor)
        assert tool is not None and tool.name == name
