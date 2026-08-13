"""Deterministic code and skill-data inputs for release artifacts."""

from __future__ import annotations

from pathlib import Path

_RUNTIME_PACKAGE_NAMES = (
    "integrations",
    "surfaces.interactive_shell",
    "tools",
)
_ACTION_SKILLS_DIR = Path("core/agent_harness/prompts/skills")
_SKILL_DATA_ROOTS = (Path("integrations"), Path("tools"))
_RUNTIME_DISCOVERY_EXCLUSIONS = frozenset({"investigation_registry", "registry.py"})


def _module_name(repo_root: Path, source_path: Path) -> str:
    relative = source_path.relative_to(repo_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def runtime_hidden_imports(repo_root: Path) -> tuple[str, ...]:
    """Return every module under packages discovered dynamically at runtime.

    PyInstaller's ``collect-submodules`` imports packages while enumerating
    them. That can silently omit OpenSRE packages when the stdlib ``platform``
    module wins over the first-party package during analysis. Walking source
    paths avoids executing application imports while producing the same module
    inventory for PyInstaller's ``hiddenimports`` input.
    """
    modules: set[str] = set()
    for package_name in _RUNTIME_PACKAGE_NAMES:
        package_root = repo_root / Path(*package_name.split("."))
        for source_path in package_root.rglob("*.py"):
            if (
                "__pycache__" in source_path.parts
                or _RUNTIME_DISCOVERY_EXCLUSIONS.intersection(source_path.parts)
                or source_path.stem.endswith("_test")
            ):
                continue
            module_name = _module_name(repo_root, source_path)
            if module_name:
                modules.add(module_name)
    return tuple(sorted(modules))


def required_skill_files(repo_root: Path) -> tuple[Path, ...]:
    """Return built-in action skills and tool workflow-guidance data files."""
    files = set((repo_root / _ACTION_SKILLS_DIR).rglob("*.md"))
    for relative_root in _SKILL_DATA_ROOTS:
        files.update((repo_root / relative_root).rglob("SKILL.md"))
    return tuple(sorted(path for path in files if path.is_file()))


def skill_data_entries(repo_root: Path) -> tuple[tuple[str, str], ...]:
    """Return PyInstaller ``datas`` entries preserving repo-relative paths."""
    return tuple(
        (str(path), str(path.parent.relative_to(repo_root)))
        for path in required_skill_files(repo_root)
    )


__all__ = ["required_skill_files", "runtime_hidden_imports", "skill_data_entries"]
