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
    "tasks",
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
        # Skip this test file itself — docstrings deliberately mention removed
        # framework names as part of regression test documentation.
        if path.name == "test_removed_architecture_references.py":
            continue
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


def test_deleted_pipeline_graph_module_is_not_referenced() -> None:
    """Regression test for Sentry issue #2469.

    `app/pipeline/graph.py` and `app/graph_pipeline.py` were deleted in the
    LangGraph migration (commit 57a5cbe0).  The error that triggered #2469 was
    ``_traced_node`` being called inside ``build_graph()`` in ``graph.py``
    without ever being imported — a ``NameError`` that crashed LangGraph
    Cloud's server on startup.

    Guard against either deleted module being silently re-introduced.
    """
    deleted_modules = ("app.pipeline." + "graph", "app." + "graph_pipeline")
    offenders: list[str] = []

    for path in _iter_repo_text_files():
        # Skip this test file itself — it intentionally names the deleted modules.
        if path.name == "test_removed_architecture_references.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(mod in text for mod in deleted_modules):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == [], (
        "Deleted LangGraph modules re-appeared in the codebase. "
        "See Sentry issue #2469 and commit 57a5cbe0 for context."
    )


def test_traced_node_is_importable_from_runners() -> None:
    """Regression test for Sentry issue #2469.

    `_traced_node` must always be importable from `app.pipeline.runners`.
    The original bug was that ``graph.py`` called ``_traced_node(...)`` at
    module level (via ``graph = build_graph()`` at the bottom of the file)
    without importing it, causing a ``NameError`` that surfaced as a
    ``GraphLoadError`` in LangGraph Cloud's lifespan startup.
    """
    from app.pipeline import runners  # noqa: PLC0415

    assert callable(getattr(runners, "_traced_node", None)), (
        "_traced_node must be defined and callable in app.pipeline.runners"
    )
