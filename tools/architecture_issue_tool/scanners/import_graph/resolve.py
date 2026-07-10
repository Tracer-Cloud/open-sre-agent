"""Map files and import strings to architectural units."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners.import_graph.package_index import (
    _SOURCE_ROOT_MARKERS,
    resolve_jvm_import,
)

_ARCH_ROOTS = frozenset({"src", "lib", "cmd", "internal", "pkg", "app", "packages"})


def unit_for_file(clone_root: Path, file_path: Path) -> str | None:
    """Return the architectural unit for *file_path* relative to *clone_root*."""
    root = clone_root.resolve()
    path = file_path.resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    relative = rel.as_posix()
    for marker in _SOURCE_ROOT_MARKERS:
        needle = f"/{marker}/"
        if needle in f"/{relative}/":
            unit_path = relative.split(f"/{marker}/", 1)[0]
            return unit_path.strip("/") or None

    parts = rel.parts
    if not parts:
        return None
    if parts[0] in _ARCH_ROOTS and len(parts) > 1:
        return parts[1]
    if len(parts) >= 2:
        return parts[0]
    return parts[0]


def _unit_from_path(clone_root: Path, path: Path, known_units: set[str]) -> str | None:
    root = clone_root.resolve()
    resolved = path.resolve()
    unit = unit_for_file(root, resolved)
    if unit and unit in known_units:
        return unit
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return None
    for part in rel.parts:
        if part in known_units:
            return part
    return unit


def resolve_import_to_unit(
    clone_root: Path,
    source_file: Path,
    import_spec: str,
    known_units: set[str],
    *,
    package_index: dict[str, str] | None = None,
) -> str | None:
    """Resolve *import_spec* from *source_file* to a known architectural unit."""
    cleaned = import_spec.strip().strip('"').strip("'").strip("`")
    if not cleaned:
        return None

    if package_index:
        jvm_unit = resolve_jvm_import(package_index, cleaned)
        if jvm_unit is not None and jvm_unit in known_units:
            return jvm_unit

    if cleaned.startswith("."):
        target = (source_file.parent / cleaned).resolve()
        unit = _unit_from_path(clone_root, target, known_units)
        if unit:
            return unit
        if target.suffix:
            return _unit_from_path(clone_root, target, known_units)
        for suffix in (".ts", ".tsx", ".js", ".py", ".go", ".rs", ".java"):
            candidate = target.with_suffix(suffix)
            if candidate.is_file():
                return _unit_from_path(clone_root, candidate, known_units)
        parent = target.parent
        if parent != target:
            return _unit_from_path(clone_root, parent, known_units)
        return _unit_from_path(clone_root, target, known_units)

    if cleaned.startswith("@"):
        cleaned = cleaned.lstrip("@/")

    segments = cleaned.replace("\\", "/").split("/")
    if segments and segments[0] in known_units:
        return segments[0]

    if "." in cleaned and "/" not in cleaned:
        first = cleaned.split(".")[0]
        if first in known_units:
            return first

    for segment in reversed(segments):
        if segment in known_units:
            return segment

    for unit in known_units:
        if f"/{unit}/" in f"/{cleaned}/" or cleaned.endswith(f"/{unit}"):
            return unit

    return None
