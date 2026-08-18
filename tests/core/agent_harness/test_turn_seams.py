"""Guard the route / execute / finalize separation of concerns."""

from __future__ import annotations

import ast
from pathlib import Path

from core.agent_harness.prompts.action.multi_step_policy import (
    ACTION_CONVERSATIONAL_SESSION_GOAL_RULE,
    ACTION_LOCAL_SHELL_MULTI_STEP_RULE,
)
from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE
from core.agent_harness.turns import answer_finalize, orchestrator, turn_route

_TURNS = Path(orchestrator.__file__).resolve().parent


def test_orchestrator_does_not_gate_gather_route_on_stream_rewrite_flag() -> None:
    """Anti-regression for UnboundLocalError on live oracle 346.

    ``text_changed_after_streaming`` is finalize-only; routing must not read it.
    """
    src = Path(orchestrator.__file__).read_text(encoding="utf-8")
    assert "text_changed_after_streaming" not in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            text = ast.dump(node)
            assert "text_changed_after_streaming" not in text


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_route_module_has_no_stream_or_cta_imports() -> None:
    imported = _imported_module_names(Path(turn_route.__file__))
    assert not any("answer_finalize" in name for name in imported)
    assert not any("pending_offer" in name for name in imported)
    assert not any("evidence_need" in name for name in imported)


def test_finalize_module_does_not_import_route_decision_helpers_circularly() -> None:
    # Finalize may use RouteIntent typing from turn_route, but must not call
    # route_turn (that would re-entangle decide and finalize).
    src = Path(answer_finalize.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    assert "route_turn" not in called
    assert "routing_input_from_result" not in called


def test_multi_step_prompt_fragments_are_both_present_and_distinct() -> None:
    assert "shell_run" in ACTION_LOCAL_SHELL_MULTI_STEP_RULE
    assert "LOCAL SEQUENTIAL STEPS" in ACTION_LOCAL_SHELL_MULTI_STEP_RULE
    assert "session_goal" in ACTION_CONVERSATIONAL_SESSION_GOAL_RULE
    assert "NOT\nshell_run" in ACTION_CONVERSATIONAL_SESSION_GOAL_RULE or (
        "NOT shell_run" in ACTION_CONVERSATIONAL_SESSION_GOAL_RULE.replace("\n", " ")
    )
    # Assembled prompt includes both contracts in order.
    shell_at = _SYSTEM_PROMPT_BASE.index("LOCAL SEQUENTIAL STEPS")
    goal_at = _SYSTEM_PROMPT_BASE.index("Conversational keep-going checklists")
    assert shell_at < goal_at


def test_seam_modules_exist() -> None:
    assert (_TURNS / "turn_route.py").is_file()
    assert (_TURNS / "answer_finalize.py").is_file()
    assert (_TURNS / "handoff_policy.py").is_file()
