from __future__ import annotations

from pathlib import Path

from tools.registry import clear_tool_registry_cache, get_registered_tools


def test_core_orchestration_import_path_removed() -> None:
    forbidden_import = "core." + "orchestration"
    tracked_roots = (
        Path("cli"),
        Path("context"),
        Path("core"),
        Path("infra"),
        Path("integrations"),
        Path("interactive_shell"),
        Path("platform"),
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
    tools_by_name = {tool.name: tool for tool in get_registered_tools(surface="chat")}

    investigation_tool = tools_by_name["run_investigation"]
    assert investigation_tool.origin_module == "tools.investigation"
    assert investigation_tool.surfaces == ("chat",)
