"""Shared strict JSON Schema contract for investigation tool definitions.

Used by provider-specific normalizers (e.g. strict HTTP tool-spec APIs) and by the
registry-wide test in ``test_investigation_tool_schemas.py``. Not tied to a single vendor.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from tools.registry import get_registered_tools

# Keys stripped by the strictest investigation schema normalizer in the tree today.
# When a new adapter is stricter, extend this set and the assertions below together.
STRICT_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "title",
        "$schema",
        "$defs",
        "definitions",
        "$ref",
        "not",
        "nullable",
    }
)


def assert_strict_tool_schema_node(node: Any, *, path: str) -> None:
    """Enforce invariants strict LLM tool-schema APIs expect (string ``type``, typed arrays)."""
    if not isinstance(node, dict):
        return
    for key in node:
        assert key not in STRICT_UNSUPPORTED_SCHEMA_KEYS, f"{path}: unsupported key {key!r}"

    schema_type = node.get("type")
    assert not isinstance(schema_type, list), f"{path}: type must not be a list {schema_type!r}"
    if "properties" in node:
        assert schema_type == "object", f"{path}: properties without type object"
        properties = node["properties"]
        assert isinstance(properties, dict), f"{path}: properties must be a dict"
        for name, child in properties.items():
            assert_strict_tool_schema_node(child, path=f"{path}.{name}")

    if schema_type == "array":
        items = node.get("items")
        assert isinstance(items, dict), f"{path}: array missing typed items"
        assert "type" in items or "properties" in items, f"{path}: array items lack type"
        assert_strict_tool_schema_node(items, path=f"{path}[]")
    elif isinstance(node.get("items"), dict):
        assert_strict_tool_schema_node(node["items"], path=f"{path}[]")


def assert_all_investigation_tools_satisfy_strict_adapter(
    *,
    normalize_schema: Callable[[dict[str, Any] | None], dict[str, Any]],
    build_tool_specs: Callable[[list[Any]], list[dict[str, Any]]],
) -> None:
    """Every investigation tool must normalize and serialize under *strict* adapter rules."""
    tools = get_registered_tools("investigation")
    assert tools, "expected at least one investigation tool"

    for tool in tools:
        schema = normalize_schema(tool.public_input_schema)
        assert schema.get("type") == "object", tool.name
        assert isinstance(schema.get("properties"), dict), tool.name
        assert_strict_tool_schema_node(schema, path=tool.name)

    specs = build_tool_specs(tools)
    assert len(specs) == len(tools)
    json.dumps({"tools": specs})
