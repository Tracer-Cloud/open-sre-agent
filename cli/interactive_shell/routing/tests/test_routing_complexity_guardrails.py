"""Static complexity guardrails for routing policy modules."""

from __future__ import annotations

import ast
from pathlib import Path

_COMPLEXITY_LIMITS: dict[str, int] = {
    "cli/interactive_shell/routing/handle_message_with_agent/orchestration/llm_action_planner/postprocessing.py": 5,
}


_DECISION_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.BoolOp,
    ast.IfExp,
    ast.Match,
    ast.comprehension,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[4]


def _complexity(node: ast.AST) -> int:
    return 1 + sum(1 for child in ast.walk(node) if isinstance(child, _DECISION_NODES))


def test_routing_module_complexity_guardrails() -> None:
    repo_root = _repo_root()
    violations: list[str] = []

    for rel_path, max_allowed in _COMPLEXITY_LIMITS.items():
        abs_path = repo_root / rel_path
        tree = ast.parse(abs_path.read_text(encoding="utf-8"), filename=str(abs_path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            score = _complexity(node)
            if score > max_allowed:
                violations.append(
                    f"{rel_path}:{node.name} complexity {score} exceeds max {max_allowed}"
                )

    assert not violations, "\n".join(violations)
