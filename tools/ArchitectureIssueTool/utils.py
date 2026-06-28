from pathlib import Path

_SKIP_ROOT_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "docs",
        "infra",
        "opensre.egg-info",
        "packaging",
        "tests",
        "venv",
    }
)

_FORBIDDEN_RULES: dict[str, set[str]] = {
    "core": {"integrations", "tools", "cli"},
    "integrations": {"tools", "cli"},
    "tools": {"cli"},
    "platform": {"core", "integrations", "tools", "cli"},
    "config": {"core", "integrations", "tools", "cli", "platform", "infra"},
}

_BASELINE_IGNORES: set[str] = {
    "integrations.hermes.sinks -> tools.watch_dog.alarms",
    "integrations.cli -> cli.wizard.integration_health",
}


def discover_packages(repo_root: Path) -> list[str]:
    """Dynamically discover package roots in the repository."""
    packages = []
    if not repo_root.exists():
        return list(_FORBIDDEN_RULES.keys())
    for child in repo_root.iterdir():
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name not in _SKIP_ROOT_DIRS
            and any(child.rglob("*.py"))
        ):
            packages.append(child.name)
    return sorted(packages)


def get_module_path(file_path: Path, repo_root: Path) -> str:
    """Compute the python module path for a file relative to the repo root."""
    try:
        rel_parts = file_path.relative_to(repo_root).with_suffix("").parts
        module_path = ".".join(rel_parts)
        if rel_parts and rel_parts[-1] == "__init__":
            module_path = ".".join(rel_parts[:-1])
        return module_path
    except ValueError:
        return ""


def resolve_import(
    module_path: str, imported_module: str, level: int, is_init: bool = False
) -> str:
    """Resolve an absolute or relative import to its full absolute module path."""
    if level == 0:
        return imported_module
    parts = module_path.split(".") if module_path else []
    if is_init:
        parts.append("__init__")
    if level >= len(parts):
        return imported_module
    base = ".".join(parts[:-level])
    if base:
        if imported_module:
            return f"{base}.{imported_module}"
        return base
    return imported_module
