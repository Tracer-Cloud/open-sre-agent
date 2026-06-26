"""Canonical tool registry shared by investigation and chat surfaces."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import threading
from functools import lru_cache
from types import ModuleType

import tools as tools_package
import vendors as vendors_package
from tools.base import BaseTool
from tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool, ToolSurface

logger = logging.getLogger(__name__)

_SKIP_MODULE_NAMES = {
    "__pycache__",
    "base",
    "registry",
    "registered_tool",
    "tool_decorator",
    "investigation_registry",
    "utils",
}

# Extension point: callers outside ``tools.*`` can register additional
# tool packages by calling :func:`register_external_tool_package`.
# Registered packages are walked the same way as :mod:`tools` — each
# top-level submodule is imported and any ``@tool``-decorated callables
# are picked up.
#
# Production stays clean: with no external registrations, the registry
# discovers only ``tools.*``. The list is *not* persisted across
# processes — every fresh import of opensre starts with zero externals.
_external_tool_packages: list[ModuleType] = []
_external_registration_lock = threading.Lock()


def register_external_tool_package(package: ModuleType) -> None:
    """Register an additional tool package for registry discovery.

    Call before any ``get_registered_tools()`` consumer in the same
    process. The registry cache is cleared so the new package's tools
    appear on the next lookup.

    Idempotent and thread-safe: concurrent callers registering the same
    package (e.g. multiple workers in a ``ThreadPoolExecutor`` each
    importing the same extension on first use) won't add duplicate
    entries that would otherwise produce noisy ``Duplicate tool name``
    warnings on every subsequent registry walk.

    Production code does NOT call this — it's an extension point for
    callers outside ``tools.*`` that ship their own tools but want
    them routed through opensre's agent loop.
    """
    with _external_registration_lock:
        if package in _external_tool_packages:
            return
        _external_tool_packages.append(package)
        clear_tool_registry_cache()


def _iter_tool_module_names(package: ModuleType) -> list[str]:
    module_names: list[str] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in _SKIP_MODULE_NAMES:
            continue
        if module_info.name.startswith("_") or module_info.name.endswith("_test"):
            continue
        module_names.append(module_info.name)
    return sorted(module_names)


def _import_tool_module(package: ModuleType, module_name: str) -> ModuleType:
    return importlib.import_module(f"{package.__name__}.{module_name}")


def _candidate_belongs_to_module(candidate: object, module_name: str) -> bool:
    if isinstance(candidate, BaseTool):
        return candidate.__class__.__module__ == module_name
    return getattr(candidate, "__module__", None) == module_name


def _default_surfaces_for_tool(_tool_name: str) -> tuple[ToolSurface, ...]:
    return ("investigation",)


def _registered_tool_from_candidate(candidate: object) -> RegisteredTool | None:
    registered = getattr(candidate, REGISTERED_TOOL_ATTR, None)
    if isinstance(registered, RegisteredTool):
        return registered

    if isinstance(candidate, BaseTool):
        explicit_surfaces = getattr(candidate, "surfaces", None) or getattr(
            candidate.__class__,
            "surfaces",
            None,
        )
        return RegisteredTool.from_base_tool(
            candidate,
            surfaces=explicit_surfaces or _default_surfaces_for_tool(candidate.name),
        )

    return None


def _collect_registered_tools_from_module(module: ModuleType) -> list[RegisteredTool]:
    tools_by_name: dict[str, RegisteredTool] = {}
    seen_candidate_ids: set[int] = set()

    for _, candidate in inspect.getmembers(module):
        if not _candidate_belongs_to_module(candidate, module.__name__):
            continue
        candidate_id = id(candidate)
        if candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate_id)
        registered = _registered_tool_from_candidate(candidate)
        if registered is None:
            continue
        if registered.name in tools_by_name:
            logger.warning(
                "[tools] Duplicate tool name '%s' in module %s; keeping first definition",
                registered.name,
                module.__name__,
            )
            continue
        tools_by_name[registered.name] = registered

    return sorted(tools_by_name.values(), key=lambda tool: tool.name)


@lru_cache(maxsize=1)
def _load_registry_snapshot() -> tuple[RegisteredTool, ...]:
    tools_by_name: dict[str, RegisteredTool] = {}

    # Walk the canonical tools package, then the vendor surfaces package,
    # then any externally-registered packages in registration order.
    # First definition of a given tool name wins; duplicates are logged and skipped.
    packages: list[ModuleType] = [tools_package, vendors_package, *_external_tool_packages]
    for package in packages:
        for module_name in _iter_tool_module_names(package):
            try:
                module = _import_tool_module(package, module_name)
            except ModuleNotFoundError as exc:
                logger.warning("[tools] Skipping %s.%s: %s", package.__name__, module_name, exc)
                continue
            except Exception as exc:
                logger.warning(
                    "[tools] Skipping %s.%s due to import failure: %s",
                    package.__name__,
                    module_name,
                    exc,
                    exc_info=True,
                )
                continue

            for tool in _collect_registered_tools_from_module(module):
                if tool.name in tools_by_name:
                    logger.warning(
                        "[tools] Duplicate tool name '%s' across modules; keeping first definition",
                        tool.name,
                    )
                    continue
                tools_by_name[tool.name] = tool

    return tuple(sorted(tools_by_name.values(), key=lambda tool: tool.name))


@lru_cache(maxsize=1)
def _load_registry_tool_map() -> dict[str, RegisteredTool]:
    return {tool.name: tool for tool in _load_registry_snapshot()}


def clear_tool_registry_cache() -> None:
    _load_registry_snapshot.cache_clear()
    _load_registry_tool_map.cache_clear()


def get_registered_tools(surface: ToolSurface | None = None) -> list[RegisteredTool]:
    tools = list(_load_registry_snapshot())
    if surface is None:
        return tools
    return [tool for tool in tools if surface in tool.surfaces]


def get_registered_tool_map(surface: ToolSurface | None = None) -> dict[str, RegisteredTool]:
    if surface is None:
        return dict(_load_registry_tool_map())
    return {tool.name: tool for tool in get_registered_tools(surface)}


def resolve_tool_display_name(tool_name: str) -> str:
    """Return a human-friendly label for a tool name."""
    tool = _load_registry_tool_map().get(tool_name)
    if tool is not None:
        return tool.display_name or tool.name.replace("_", " ")
    return tool_name.replace("_", " ")
