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
        "node_modules",
        "opensre.egg-info",
        "packaging",
        "venv",
    }
)


def rel_path(clone_root: Path, path: Path) -> str:
    """Return a repo-relative POSIX path string."""
    return path.relative_to(clone_root).as_posix()


def should_skip_path(path: Path) -> bool:
    """True when *path* should be excluded from architecture scans."""
    return any(part in _SKIP_PATH_PARTS for part in path.parts)


def iter_py_files(_clone_root: Path, scan_roots: list[Path]) -> Iterator[Path]:
    """Yield Python files under *scan_roots*, excluding common noise paths."""
    seen: set[Path] = set()
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if should_skip_path(path):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path
