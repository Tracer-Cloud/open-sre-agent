"""Git-tracked file listing for source-invariant guard tests.

Guard tests scan the repository for forbidden imports or tokens. Walking the
live working tree with ``Path.rglob`` races with ``.py`` files that other tests
write under ``pytest -n auto``, which flakes the scan. Restricting to
git-tracked files makes the scan deterministic and matches the intent — these
invariants hold over committed source, not transient scratch files.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def tracked_files(root: str) -> frozenset[Path]:
    """Absolute paths of every git-tracked file under ``root``."""
    result = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    base = Path(root)
    return frozenset(base / rel for rel in result.stdout.split("\0") if rel)


def tracked_python_files(root: str) -> list[Path]:
    """Sorted git-tracked ``*.py`` files under ``root``."""
    return sorted(path for path in tracked_files(root) if path.suffix == ".py")
