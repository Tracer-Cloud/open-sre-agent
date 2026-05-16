from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-devcontainer",
    "__pycache__",
    "build",
    "htmlcov",
    "node_modules",
    "opensre.egg-info",
    "plans",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".mdc",
    ".mdx",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _iter_repo_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        parts = path.parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        if "site-packages" in parts:
            continue
        if not path.is_file():
            continue
        if path.name in {"Dockerfile", "Makefile"} or path.suffix in TEXT_SUFFIXES:
            files.append(path)
    return files


def test_removed_framework_names_do_not_reappear() -> None:
    removed = ("lang" + "graph", "lang" + "chain", "lang" + "smith")
    offenders: list[str] = []

    for path in _iter_repo_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if any(token in text for token in removed):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_deleted_app_nodes_package_is_not_referenced_by_python_code() -> None:
    deleted_package = "app." + "nodes"
    offenders: list[str] = []

    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if deleted_package in text:
            offenders.append(str(path.relative_to(ROOT)))

    for path in (ROOT / "tests").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if deleted_package in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
