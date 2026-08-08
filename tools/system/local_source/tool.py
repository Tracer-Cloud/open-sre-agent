"""Registered investigation tools for a configured local source tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.tool_framework.tool_decorator import tool
from tools.system.local_source.repository import LocalSourceError
from tools.system.local_source.repository import list_tree as _list_tree
from tools.system.local_source.repository import read_file as _read_file
from tools.system.local_source.repository import search as _search

_SOURCE = "local_source"
_INJECTED_PARAMS = ("root_path",)


def _available(sources: dict[str, dict]) -> bool:
    source = sources.get(_SOURCE, {})
    root_path = str(source.get("root_path") or "")
    return bool(source.get("connection_verified") and root_path and Path(root_path).is_dir())


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {"root_path": str(sources[_SOURCE]["root_path"])}


def _failure(exc: LocalSourceError, *, empty_key: str, empty_value: Any) -> dict[str, Any]:
    return {
        "source": _SOURCE,
        "available": False,
        "error": str(exc),
        empty_key: empty_value,
    }


@tool(
    name="list_local_source_tree",
    source=_SOURCE,
    description="List files and directories within the configured local source repository.",
    use_cases=[
        "Understanding repository structure during an incident",
        "Finding application, configuration, and deployment source paths",
    ],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    evidence_type="artifact",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": ""},
            "depth": {"type": "integer", "default": 2},
            "limit": {"type": "integer", "default": 200},
            "root_path": {"type": "string"},
        },
        "required": [],
    },
    injected_params=_INJECTED_PARAMS,
    is_available=_available,
    extract_params=_extract_params,
)
def list_local_source_tree(
    path: str = "",
    depth: int = 2,
    limit: int = 200,
    root_path: str = "",
) -> dict[str, Any]:
    """List a bounded source subtree using repository-relative paths."""
    try:
        return _list_tree(root_path, path=path, depth=depth, limit=limit)
    except LocalSourceError as exc:
        return _failure(exc, empty_key="entries", empty_value=[])


@tool(
    name="search_local_source",
    source=_SOURCE,
    description="Search text literally across the configured local source repository.",
    use_cases=[
        "Finding code related to errors, stack frames, routes, or service behavior",
        "Locating configuration or feature-flag definitions during an incident",
    ],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    evidence_type="artifact",
    requires=["query"],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "default": ""},
            "file_glob": {"type": "string", "default": "*"},
            "limit": {"type": "integer", "default": 100},
            "root_path": {"type": "string"},
        },
        "required": ["query"],
    },
    injected_params=_INJECTED_PARAMS,
    is_available=_available,
    extract_params=_extract_params,
)
def search_local_source(
    query: str,
    path: str = "",
    file_glob: str = "*",
    limit: int = 100,
    root_path: str = "",
) -> dict[str, Any]:
    """Return bounded repository-relative line matches."""
    try:
        return _search(
            root_path,
            query=query,
            path=path,
            file_glob=file_glob,
            limit=limit,
        )
    except LocalSourceError as exc:
        return _failure(exc, empty_key="matches", empty_value=[])


@tool(
    name="read_local_source_file",
    source=_SOURCE,
    description="Read a bounded line range from one configured local source file.",
    use_cases=[
        "Inspecting source found through local repository search",
        "Tracing an observed failure through application or configuration code",
    ],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    evidence_type="artifact",
    requires=["path"],
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "default": 1},
            "end_line": {"type": "integer", "default": 0},
            "root_path": {"type": "string"},
        },
        "required": ["path"],
    },
    injected_params=_INJECTED_PARAMS,
    is_available=_available,
    extract_params=_extract_params,
)
def read_local_source_file(
    path: str,
    start_line: int = 1,
    end_line: int = 0,
    root_path: str = "",
) -> dict[str, Any]:
    """Read source without permitting access outside the configured root."""
    try:
        return _read_file(
            root_path,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
    except LocalSourceError as exc:
        return _failure(exc, empty_key="content", empty_value="")
