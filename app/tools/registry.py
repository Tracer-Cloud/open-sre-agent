"""Canonical tool registry shared by investigation and chat surfaces."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from contextlib import suppress
from functools import lru_cache
from types import ModuleType

import sentry_sdk

from app import tools as tools_package
from app.tools.base import BaseTool
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool, ToolSurface
from app.utils.errors import report_exception

logger = logging.getLogger(__name__)


def _classify_import_error(exc: BaseException) -> tuple[str, dict[str, str]]:
    """Classify a tool-module import failure for Sentry routing (#1464).

    External ``ModuleNotFoundError`` (e.g. optional integration extras like
    ``psycopg2``, ``boto3`` plugins not installed in this environment) is a
    user-environment signal, captured at ``warning`` severity so it does not
    drown real bugs. Anything else — an internal ``app.*`` import failure or
    any other exception raised during module import — is a regression in our
    own code and is captured at ``error`` severity.
    """
    if isinstance(exc, ModuleNotFoundError) and exc.name and not exc.name.startswith("app."):
        return "warning", {
            "event": "optional_dependency_missing",
            "missing_module": exc.name,
        }
    return "error", {"event": "tool_module_import_failed"}


def _record_import_health(import_failures: int) -> None:
    """Publish aggregate tool-registry import health as a Sentry tag (#1464).

    Always set — including ``"0"`` on a clean rebuild — so a previously written
    failure count does not leak across cache rebuilds. ``sentry_sdk.set_tag``
    mutates the currently active scope; when ``clear_tool_registry_cache()`` is
    invoked from inside a request context, the tag attaches only to that
    request, which is acceptable for dashboards but is the reason this helper
    rewrites unconditionally rather than skipping on zero. Best-effort: a
    Sentry tag write must never break registry initialisation.
    """
    with suppress(Exception):
        import sentry_sdk  # noqa: PLC0415 — lazy import, SDK may be absent

        sentry_sdk.set_tag("tools.import_failures", str(import_failures))


_SKIP_MODULE_NAMES = {
    "__pycache__",
    "base",
    "registry",
    "registered_tool",
    "tool_decorator",
    "investigation_registry",
    "utils",
}

# Preserve the current chat surface while the repo migrates toward explicit
# per-tool surface metadata.
_LEGACY_CHAT_TOOL_NAMES = {
    "fetch_failed_run",
    "get_tracer_run",
    "get_tracer_tasks",
    "get_failed_jobs",
    "get_failed_tools",
    "get_error_logs",
    "get_batch_statistics",
    "get_host_metrics",
    "search_github_code",
    "get_github_file_contents",
    "get_github_repository_tree",
    "list_github_commits",
    "search_sentry_issues",
    "get_sentry_issue_details",
    "list_sentry_issue_events",
}


def _iter_tool_module_names() -> list[str]:
    module_names: list[str] = []
    for module_info in pkgutil.iter_modules(tools_package.__path__):
        if module_info.name in _SKIP_MODULE_NAMES:
            continue
        if module_info.name.startswith("_") or module_info.name.endswith("_test"):
            continue
        module_names.append(module_info.name)
    return sorted(module_names)


def _import_tool_module(module_name: str) -> ModuleType:
    return importlib.import_module(f"{tools_package.__name__}.{module_name}")


def _candidate_belongs_to_module(candidate: object, module_name: str) -> bool:
    if isinstance(candidate, BaseTool):
        return candidate.__class__.__module__ == module_name
    return getattr(candidate, "__module__", None) == module_name


def _default_surfaces_for_tool(tool_name: str) -> tuple[ToolSurface, ...]:
    if tool_name in _LEGACY_CHAT_TOOL_NAMES:
        return ("investigation", "chat")
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
    import_failures = 0

    for module_name in _iter_tool_module_names():
        try:
            module = _import_tool_module(module_name)
        except Exception as exc:
            severity, extra_tags = _classify_import_error(exc)
            report_exception(
                exc,
                logger=logger,
                message=f"[tools] Skipping {module_name} due to import failure",
                severity=severity,
                tags={
                    "surface": "tool",
                    "component": f"app.tools.{module_name}",
                    "module_name": module_name,
                    **extra_tags,
                },
            )
            import_failures += 1
            continue

        for tool in _collect_registered_tools_from_module(module):
            if tool.name in tools_by_name:
                logger.warning(
                    "[tools] Duplicate tool name '%s' across modules; keeping first definition",
                    tool.name,
                )
                continue
            tools_by_name[tool.name] = tool

    _record_import_health(import_failures)

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
