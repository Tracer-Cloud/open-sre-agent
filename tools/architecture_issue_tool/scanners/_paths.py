"""Shared path helpers for architecture scanners."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

_SKIP_PATH_PARTS = frozenset(
    {
        "__pycache__",
        ".git",
        ".github",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "node_modules",
        "opensre.egg-info",
        "packaging",
        "target",
        "vendor",
        "venv",
    }
)

_SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".kt",
        ".kts",
        ".swift",
        ".scala",
        ".sc",
        ".sh",
        ".bash",
        ".lua",
    }
)


def rel_path(clone_root: Path, path: Path) -> str:
    """Return a repo-relative POSIX path string."""
    return path.relative_to(clone_root).as_posix()


def should_skip_path(path: Path) -> bool:
    """True when *path* should be excluded from architecture scans."""
    return any(part in _SKIP_PATH_PARTS for part in path.parts)


def _iter_files_with_suffixes(
    scan_roots: list[Path],
    suffixes: frozenset[str],
) -> Iterator[Path]:
    seen: set[Path] = set()
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in suffixes:
                continue
            if should_skip_path(path):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def iter_py_files(_clone_root: Path, scan_roots: list[Path]) -> Iterator[Path]:
    """Yield Python files under *scan_roots*, excluding common noise paths."""
    yield from _iter_files_with_suffixes(scan_roots, frozenset({".py"}))


def iter_source_files(clone_root: Path, scan_roots: list[Path] | None = None) -> Iterator[Path]:
    """Yield supported polyglot source files under *clone_root*."""
    roots = scan_roots if scan_roots is not None else [clone_root]
    yield from _iter_files_with_suffixes(roots, _SOURCE_EXTENSIONS)
