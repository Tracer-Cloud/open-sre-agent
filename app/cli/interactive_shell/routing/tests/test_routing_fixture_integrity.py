"""Guardrails for routing scenario directories and test hygiene."""

from __future__ import annotations

import ast
from pathlib import Path

from app.cli.interactive_shell.routing.tests.scenario_loader import (
    INTENT_TO_BEHAVIOR_CLASS,
    SCENARIOS_DIR,
    load_all_scenarios,
    validate_action_shape,
)

TESTS_DIR = Path(__file__).resolve().parent
ROUTING_SCENARIOS_TEST = TESTS_DIR / "test_routing_scenarios.py"
ORACLE_RUNTIME = TESTS_DIR / "_oracle_runtime.py"


def _mock_policy_violations(module_path: Path) -> list[str]:
    source = module_path.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(module_path))
    violations: list[str] = []

    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "unittest.mock":
                    violations.append("unittest.mock import")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "unittest.mock":
                violations.append("unittest.mock from-import")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"patch", "MagicMock"}:
                violations.append(f"{func.id} call")
            elif isinstance(func, ast.Attribute) and func.attr in {"patch", "MagicMock"}:
                violations.append(f"{func.attr} attribute call")

    return violations


def test_every_scenario_has_paired_files() -> None:
    violations: list[str] = []
    for behavior_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not behavior_dir.is_dir():
            continue
        for scenario_dir in sorted(behavior_dir.iterdir()):
            if not scenario_dir.is_dir():
                continue
            scenario_file = scenario_dir / "scenario.yml"
            answer_file = scenario_dir / "answer.yml"
            if not scenario_file.is_file():
                violations.append(f"{scenario_dir}: missing scenario.yml")
            if not answer_file.is_file():
                violations.append(f"{scenario_dir}: missing answer.yml")
    assert not violations, "scenario pairing violations:\n" + "\n".join(violations)


def test_scenario_ids_are_globally_unique() -> None:
    cases = load_all_scenarios()
    ids = [case.scenario.id for case in cases]
    assert len(ids) == len(set(ids))


def test_scenario_directory_name_matches_id() -> None:
    cases = load_all_scenarios()
    for case in cases:
        assert case.scenario.scenario_dir.name == case.scenario.id


def test_scenario_class_matches_directory() -> None:
    cases = load_all_scenarios()
    for case in cases:
        expected = INTENT_TO_BEHAVIOR_CLASS[case.scenario.intent_class]
        assert case.scenario.behavior_class == expected


def test_planned_and_executed_action_shapes() -> None:
    violations: list[str] = []
    for case in load_all_scenarios():
        scenario_id = case.scenario.id
        for index, action in enumerate(case.answer.planned_actions):
            try:
                validate_action_shape(
                    dict(action),
                    prefix=f"{scenario_id} planned_actions[{index}]",
                    require_source=True,
                )
            except ValueError as exc:
                violations.append(str(exc))
        for index, action in enumerate(case.answer.executed_actions):
            try:
                validate_action_shape(
                    dict(action),
                    prefix=f"{scenario_id} executed_actions[{index}]",
                    require_source=False,
                )
            except ValueError as exc:
                violations.append(str(exc))
    assert not violations, "action shape violations:\n" + "\n".join(violations)


def test_should_execute_invariants() -> None:
    violations: list[str] = []
    for case in load_all_scenarios():
        scenario_id = case.scenario.id
        policy = case.answer.policy
        if policy.has_unhandled_clause and policy.should_execute:
            violations.append(f"{scenario_id}: has_unhandled_clause requires should_execute=false")
        if not policy.should_execute and case.answer.executed_actions:
            violations.append(f"{scenario_id}: should_execute=false requires executed_actions=[]")
        must_not = case.answer.response_contract.get("must_not_contain", [])
        if not policy.should_execute and "$ /" not in must_not:
            violations.append(f"{scenario_id}: non-executing cases must include '$ /' in must_not_contain")
    assert not violations, "policy invariant violations:\n" + "\n".join(violations)


def test_routing_test_modules_do_not_use_mock_patterns() -> None:
    violations: list[str] = []
    for test_path in (ROUTING_SCENARIOS_TEST, ORACLE_RUNTIME):
        if not test_path.exists():
            continue
        for violation in _mock_policy_violations(test_path):
            violations.append(f"{test_path.name}: found disallowed {violation}")
    assert not violations, (
        "No-mocks policy violated in routing tests. "
        "Remove mock usage from canonical routing suites.\n" + "\n".join(violations)
    )
