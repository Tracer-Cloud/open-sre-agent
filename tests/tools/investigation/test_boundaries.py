from __future__ import annotations

import ast
from pathlib import Path

from core.domain.types.tools import ToolSurface
from tools.registry import clear_tool_registry_cache, get_registered_tools


def test_core_orchestration_import_path_removed() -> None:
    forbidden_import = "core." + "orchestration"
    tracked_roots = (
        Path("cli"),
        Path("context"),
        Path("core"),
        Path("integrations"),
        Path("interactive_shell"),
        Path("infrastructure"),
        Path("tests"),
        Path("tools"),
    )
    offenders: list[Path] = []
    for root in tracked_roots:
        for path in root.glob("**/*.py"):
            if "__pycache__" in path.parts:
                continue
            if forbidden_import in path.read_text(encoding="utf-8"):
                offenders.append(path)

    assert offenders == []


def test_investigation_tool_is_registry_discoverable() -> None:
    clear_tool_registry_cache()
    tools_by_name = {tool.name: tool for tool in get_registered_tools(surface=ToolSurface.CHAT)}

    investigation_tool = tools_by_name["run_investigation"]
    assert investigation_tool.origin_module == "tools.investigation"
    assert investigation_tool.surfaces == ("chat",)


def test_llm_calling_stages_do_not_import_the_llm_factory_directly() -> None:
    stage_paths = (
        Path("tools/investigation/stages/gather_evidence/agent.py"),
        Path("tools/investigation/stages/intake/node.py"),
        Path("tools/investigation/stages/diagnose/node.py"),
    )
    offenders: list[str] = []
    for path in stage_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imports_factory = isinstance(node, ast.ImportFrom) and node.module == "core.llm.factory"
            imports_factory = imports_factory or (
                isinstance(node, ast.Import)
                and any(alias.name == "core.llm.factory" for alias in node.names)
            )
            if imports_factory:
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == []
