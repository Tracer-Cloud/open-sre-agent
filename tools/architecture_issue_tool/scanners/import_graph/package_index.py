"""Map JVM package names to architectural units from source layout."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners._paths import iter_source_files, rel_path
from tools.architecture_issue_tool.scanners.import_graph.languages.registry import (
    extension_to_language,
)

_SOURCE_ROOT_MARKERS: tuple[str, ...] = (
    "src/main/java",
    "src/test/java",
    "src/main/scala",
    "src/test/scala",
    "src/main/kotlin",
    "src/test/kotlin",
)

_JVM_LANGUAGES = frozenset({"java", "scala", "kotlin"})


def _unit_and_package_from_relative_path(relative_path: str) -> tuple[str, str] | None:
    normalized = relative_path.replace("\\", "/")
    for marker in _SOURCE_ROOT_MARKERS:
        needle = f"/{marker}/"
        if needle not in f"/{normalized}/":
            continue
        unit_path, remainder = normalized.split(f"/{marker}/", 1)
        unit = unit_path.strip("/")
        if not unit or "/" not in remainder:
            return None
        package_path = remainder.rsplit("/", 1)[0]
        package_name = package_path.replace("/", ".")
        if not package_name:
            return None
        return unit, package_name
    return None


def build_package_index(clone_root: Path) -> dict[str, str]:
    """Build package-prefix to unit mapping from JVM source files."""
    index: dict[str, str] = {}
    root = clone_root.resolve()
    for path in iter_source_files(root):
        language = extension_to_language(path.suffix)
        if language not in _JVM_LANGUAGES:
            continue
        relative = rel_path(root, path)
        parsed = _unit_and_package_from_relative_path(relative)
        if parsed is None:
            continue
        unit, package_name = parsed
        index[package_name] = unit
    return index


def resolve_jvm_import(package_index: dict[str, str], import_spec: str) -> str | None:
    """Resolve a JVM dotted import to an architectural unit."""
    cleaned = import_spec.strip()
    if not cleaned or cleaned.startswith("."):
        return None
    parts = cleaned.split(".")
    for end in range(len(parts), 0, -1):
        prefix = ".".join(parts[:end])
        unit = package_index.get(prefix)
        if unit is not None:
            return unit
    return None
