"""Path helpers shared by E2E scenario infrastructure scripts."""

from pathlib import Path


def find_repo_root(start: str | Path) -> Path:
    """Return the repository root containing ``pyproject.toml`` and ``tests/``."""
    path = Path(start).resolve()
    for candidate in (path, *path.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "tests").is_dir():
            return candidate
    raise RuntimeError(f"Could not resolve repository root from {path}")
