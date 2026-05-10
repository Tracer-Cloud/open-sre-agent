"""Hot reload support for the interactive CLI."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.theme import DIM, WARNING

_IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}

_INTERACTIVE_RELOAD_DEPENDENTS = (
    "app.cli.interactive_shell.command_registry",
    "app.cli.interactive_shell.commands",
    "app.cli.interactive_shell.prompt_surface",
    "app.cli.interactive_shell.cli_reference",
    "app.cli.interactive_shell.agent_actions",
    "app.cli.interactive_shell.cli_agent",
    "app.cli.interactive_shell.loop",
)


@dataclass(frozen=True)
class ReloadResult:
    """Summary of one hot reload pass."""

    changed_paths: tuple[Path, ...]
    reloaded_modules: tuple[str, ...]
    errors: tuple[str, ...]


class HotReloadCoordinator:
    """Detect repo-local Python changes and reload loaded modules between turns."""

    def __init__(
        self,
        *,
        watch_root: Path | None = None,
        package_prefix: str = "app",
        dependent_modules: tuple[str, ...] = _INTERACTIVE_RELOAD_DEPENDENTS,
    ) -> None:
        self.watch_root = (watch_root or Path(__file__).resolve().parents[3] / "app").resolve()
        self.package_prefix = package_prefix
        self.dependent_modules = dependent_modules
        self._snapshot = self._scan()

    def check_and_reload(self, console: Console) -> ReloadResult | None:
        """Reload changed modules and return a summary, or ``None`` if unchanged."""
        current = self._scan()
        changed_paths = tuple(
            sorted(
                path
                for path, fingerprint in current.items()
                if self._snapshot.get(path) != fingerprint
            )
        )
        deleted_paths = tuple(path for path in self._snapshot if path not in current)
        self._snapshot = current
        if not changed_paths and not deleted_paths:
            return None

        module_names = self._loaded_modules_for_paths(changed_paths)
        if module_names:
            module_names.extend(
                name
                for name in self.dependent_modules
                if name in sys.modules and name not in module_names
            )

        reloaded: list[str] = []
        errors: list[str] = []
        for module_name in self._reload_order(module_names):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            try:
                importlib.reload(module)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{module_name}: {exc}")
                continue
            reloaded.append(module_name)

        self._clear_known_caches(errors)
        result = ReloadResult(
            changed_paths=changed_paths + deleted_paths,
            reloaded_modules=tuple(reloaded),
            errors=tuple(errors),
        )
        self._render_result(console, result)
        return result

    def _scan(self) -> dict[Path, tuple[int, int]]:
        files: dict[Path, tuple[int, int]] = {}
        if not self.watch_root.exists():
            return files
        for path in self.watch_root.rglob("*.py"):
            if self._is_ignored(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files[path.resolve()] = (stat.st_mtime_ns, stat.st_size)
        return files

    def _is_ignored(self, path: Path) -> bool:
        return any(part in _IGNORED_DIR_NAMES for part in path.parts)

    def _loaded_modules_for_paths(self, changed_paths: tuple[Path, ...]) -> list[str]:
        changed = set(changed_paths)
        module_names: list[str] = []
        for module_name, module in sys.modules.items():
            if not module_name.startswith(f"{self.package_prefix}."):
                continue
            module_path = self._module_source_path(module)
            if module_path is None or module_path not in changed:
                continue
            module_names.append(module_name)
        return sorted(module_names, key=lambda name: (name.count("."), name), reverse=True)

    def _module_source_path(self, module: ModuleType) -> Path | None:
        raw_file = getattr(module, "__file__", None)
        if not raw_file:
            return None
        path = Path(raw_file)
        if path.suffix != ".py":
            return None
        try:
            resolved = path.resolve()
        except OSError:
            return None
        try:
            resolved.relative_to(self.watch_root)
        except ValueError:
            return None
        return resolved

    def _reload_order(self, module_names: list[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(module_names))

    def _clear_known_caches(self, errors: list[str]) -> None:
        try:
            from app.tools.registry import clear_tool_registry_cache

            clear_tool_registry_cache()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"app.tools.registry.clear_tool_registry_cache: {exc}")

        try:
            from app.cli.interactive_shell.cli_reference import invalidate_cli_reference_cache

            invalidate_cli_reference_cache()
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"app.cli.interactive_shell.cli_reference.invalidate_cli_reference_cache: {exc}"
            )

    def _render_result(self, console: Console, result: ReloadResult) -> None:
        if result.errors:
            console.print()
            console.print(
                f"[{WARNING}]hot reload had {len(result.errors)} error(s); "
                "continuing with the last usable code[/]"
            )
            for error in result.errors[:5]:
                console.print(f"[{DIM}]- {escape(error)}[/]")
            return
        if result.reloaded_modules:
            console.print()
            console.print(
                f"[{DIM}]hot reload: reloaded {len(result.reloaded_modules)} module(s)[/]"
            )
