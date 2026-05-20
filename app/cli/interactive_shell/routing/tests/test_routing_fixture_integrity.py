"""Guardrails for routing scenario directories and test hygiene."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    ActionKind,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)
from app.cli.interactive_shell.routing.tests.scenario_loader import (
    INTENT_TO_BEHAVIOR_CLASS,
    SCENARIOS_DIR,
    load_all_scenarios,
    validate_action_shape,
)

TESTS_DIR = Path(__file__).resolve().parent
ROUTING_SCENARIOS_TEST = TESTS_DIR / "test_routing_scenarios.py"
LEGACY_ROUTING_TESTS_DIR = TESTS_DIR.parents[4] / "tests" / "cli" / "interactive_shell" / "routing"
ALLOWED_LEGACY_TESTS = {"test_llm_intent_classifier.py"}
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


def test_every_scenario_file_exists() -> None:
    violations: list[str] = []
    for behavior_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not behavior_dir.is_dir():
            continue
        for scenario_file in sorted(behavior_dir.iterdir()):
            if scenario_file.suffix != ".yml":
                continue
            if not scenario_file.is_file():
                violations.append(f"{scenario_file}: missing file")
    assert not violations, "scenario file violations:\n" + "\n".join(violations)


def test_scenario_ids_are_globally_unique() -> None:
    cases = load_all_scenarios()
    ids = [case.scenario.id for case in cases]
    assert len(ids) == len(set(ids))


def test_scenario_filename_matches_id() -> None:
    cases = load_all_scenarios()
    for case in cases:
        assert case.scenario.scenario_dir.stem == case.scenario.id


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


def test_scenario_action_kinds_have_registered_tools() -> None:
    missing: list[str] = []
    for case in load_all_scenarios():
        actions = [*case.answer.planned_actions, *case.answer.executed_actions]
        for action in actions:
            kind = str(action.get("kind", "")).strip()
            if not kind:
                continue
            if kind == "assistant_handoff":
                continue
            tool_name = ACTION_KIND_TO_TOOL.get(cast(ActionKind, kind))
            if tool_name is None:
                missing.append(f"{case.scenario.id}: kind {kind!r} has no tool mapping")
                continue
            if REGISTRY.get(tool_name) is None:
                missing.append(
                    f"{case.scenario.id}: kind {kind!r} mapped to missing tool {tool_name!r}"
                )
    assert not missing, "scenario action kinds missing tool registrations:\n" + "\n".join(missing)


def test_should_execute_invariants() -> None:
    violations: list[str] = []
    for case in load_all_scenarios():
        scenario_id = case.scenario.id
        policy = case.answer.policy
        # has_unhandled_clause is deprecated; when still present it must not
        # contradict should_execute.
        if policy.has_unhandled_clause and policy.should_execute:
            violations.append(f"{scenario_id}: has_unhandled_clause requires should_execute=false")
        if not policy.should_execute and case.answer.executed_actions:
            violations.append(f"{scenario_id}: should_execute=false requires executed_actions=[]")
        # The loader auto-injects "$ /" into must_not_contain when should_execute=false,
        # so this invariant always holds on loaded data.
        must_not = case.answer.response_contract.get("must_not_contain", [])
        if not policy.should_execute and "$ /" not in must_not:
            violations.append(
                f"{scenario_id}: non-executing cases must include '$ /' in must_not_contain"
            )
        # Validate forbidden_actions entries reference real action kinds.
        forbidden = case.answer.response_contract.get("forbidden_actions", [])
        from app.cli.interactive_shell.routing.tests.scenario_loader import VALID_ACTION_KINDS

        for entry in forbidden:
            if entry not in VALID_ACTION_KINDS:
                violations.append(
                    f"{scenario_id}: forbidden_actions entry {entry!r} is not a valid kind"
                )
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


def test_routing_tests_are_colocated_except_classifier_internal_suite() -> None:
    if not LEGACY_ROUTING_TESTS_DIR.exists():
        return
    unexpected = sorted(
        path.name
        for path in LEGACY_ROUTING_TESTS_DIR.glob("test_*.py")
        if path.name not in ALLOWED_LEGACY_TESTS
    )
    assert not unexpected, (
        "Routing tests must be colocated under app/cli/interactive_shell/routing/tests/. "
        "Only classifier-internal coverage remains under tests/cli/interactive_shell/routing/: "
        + ", ".join(unexpected)
    )
