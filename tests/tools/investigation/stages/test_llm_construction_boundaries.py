"""Import-boundary checks for the T-5 (#4439) LLM-construction refactor.

Confirms intake/node.py and diagnose/node.py no longer call core.llm.factory's
get_llm() directly (they route through core.agent_harness.llm_resolution
instead), and that ConnectedInvestigationAgent.run() specifically does not
call get_llm() either — scoped to that one method, since
get_investigation_agent_class() in the same file has a legitimate,
unrelated reason to call get_llm() (provider-detection to pick which agent
subclass to construct, not to build the loop itself).
"""

from __future__ import annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[4]


def _file_calls_get_llm(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "get_llm" for alias in node.names
        ):
            return True
        if isinstance(node, ast.Name) and node.id == "get_llm":
            return True
    return False


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_intake_node_does_not_call_get_llm_directly() -> None:
    path = _repo_root() / "tools" / "investigation" / "stages" / "intake" / "node.py"
    assert not _file_calls_get_llm(path), (
        f"{path} still references get_llm() directly; use "
        "core.agent_harness.llm_resolution.default_reasoning_llm_factory() instead."
    )


def test_diagnose_node_does_not_call_get_llm_directly() -> None:
    path = _repo_root() / "tools" / "investigation" / "stages" / "diagnose" / "node.py"
    assert not _file_calls_get_llm(path), (
        f"{path} still references get_llm() directly; use "
        "core.agent_harness.llm_resolution.default_reasoning_llm_factory() instead."
    )


def test_connected_investigation_agent_run_does_not_call_get_llm_directly() -> None:
    """Scoped to ConnectedInvestigationAgent.run() only.

    get_investigation_agent_class(), in the same file, legitimately calls
    get_llm() for an unrelated reason (detecting the active provider to pick
    which agent subclass to construct) and is intentionally not covered here.
    """
    path = _repo_root() / "tools" / "investigation" / "stages" / "gather_evidence" / "agent.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    run_method = _find_function(tree, "run")
    assert run_method is not None, "ConnectedInvestigationAgent.run() not found"

    offenders = [
        node.lineno
        for node in ast.walk(run_method)
        if isinstance(node, ast.Name) and node.id == "get_llm"
    ]
    assert not offenders, (
        f"ConnectedInvestigationAgent.run() calls get_llm() directly at line(s) "
        f"{offenders}; it should build its LLM via "
        "core.agent_harness.llm_resolution.default_llm_factory() and construct "
        "the loop via core.agent_harness.agent_builder.build_agent()."
    )
