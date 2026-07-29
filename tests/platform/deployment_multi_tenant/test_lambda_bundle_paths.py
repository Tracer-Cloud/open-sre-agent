"""Lambda bundles must ship every platform module their runtime code imports.

The bundling commands whitelist paths; a new cross-module import that is not
added to the whitelist only fails at Lambda INIT in AWS. This test resolves
every ``platform.*`` import found in the bundled sources and asserts the
imported module is itself part of the bundle.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_BUNDLE_SCRIPT = "platform/deployment_multi_tenant/scripts/build-lambda-bundles.sh"

_PATH_PATTERN = re.compile(r"^\s*(platform/[A-Za-z0-9_./-]+)\s*$", re.MULTILINE)

_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from\s+(platform(?:\.\w+)+)\s+import|import\s+(platform(?:\.\w+)+))",
    re.MULTILINE,
)


def _bundled_paths(bundle_script: str) -> tuple[str, ...]:
    source = (_REPO_ROOT / bundle_script).read_text()
    paths = tuple(dict.fromkeys(_PATH_PATTERN.findall(source)))
    assert paths, f"No bundled platform paths found in {bundle_script}"
    return paths


def _bundled_source_files(paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative in paths:
        target = (_REPO_ROOT / relative).resolve()
        if target.is_dir():
            files.extend(target.rglob("*.py"))
        else:
            files.append(target)
    return files


def _is_covered(module: str, bundled: tuple[str, ...]) -> bool:
    module_path = module.replace(".", "/")
    for relative in bundled:
        if relative.endswith("/__init__.py"):
            # Shipping a package __init__ only covers importing that package
            # itself, not its unshipped submodules.
            if module_path == relative.removesuffix("/__init__.py"):
                return True
            continue
        prefix = relative.removesuffix(".py")
        if module_path == prefix or module_path.startswith(f"{prefix}/"):
            return True
    return False


def test_bundle_ships_all_platform_imports() -> None:
    bundled = _bundled_paths(_BUNDLE_SCRIPT)
    missing: set[str] = set()
    for source_file in _bundled_source_files(bundled):
        for match in _IMPORT_PATTERN.finditer(source_file.read_text()):
            module = match.group(1) or match.group(2)
            if not _is_covered(module, bundled):
                missing.add(f"{module} (imported by {source_file.relative_to(_REPO_ROOT)})")
    assert not missing, (
        "Bundled runtime imports platform modules that the bundle whitelist "
        f"in {_BUNDLE_SCRIPT} does not ship:\n" + "\n".join(sorted(missing))
    )
