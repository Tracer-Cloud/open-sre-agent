"""Guardrails for routing scenario directories and test hygiene."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import yaml

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    ActionKind,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)
from cli.interactive_shell.routing.tests.scenario_loader import (
    INTENT_TO_BEHAVIOR_CLASS,
    SCENARIOS_DIR,
    load_all_scenarios,
    validate_action_shape,
)

TESTS_DIR = Path(__file__).resolve().parent
ROUTING_SCENARIOS_TEST = TESTS_DIR / "test_routing_scenarios.py"


def _repo_root() -> Path:
    for parent in TESTS_DIR.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return TESTS_DIR.parents[3]


LEGACY_ROUTING_TESTS_DIR = _repo_root() / "tests" / "cli" / "interactive_shell" / "routing"
ALLOWED_LEGACY_TESTS: set[str] = set()
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


def test_executes_terminal_action_invariants() -> None:
    violations: list[str] = []
    for case in load_all_scenarios():
        scenario_id = case.scenario.id
        policy = case.answer.policy
        if not policy.executes_terminal_action and case.answer.executed_actions:
            violations.append(
                f"{scenario_id}: executes_terminal_action=false requires executed_actions=[]"
            )
        # The loader auto-injects "$ /" into must_not_contain when
        # executes_terminal_action=false, so this invariant always holds on loaded data.
        must_not = case.answer.response_contract.get("must_not_contain", [])
        if not policy.executes_terminal_action and "$ /" not in must_not:
            violations.append(
                f"{scenario_id}: non-executing cases must include '$ /' in must_not_contain"
            )
        # Validate forbidden_actions entries reference real action kinds.
        forbidden = case.answer.response_contract.get("forbidden_actions", [])
        from cli.interactive_shell.routing.tests.scenario_loader import VALID_ACTION_KINDS

        for entry in forbidden:
            if entry not in VALID_ACTION_KINDS:
                violations.append(
                    f"{scenario_id}: forbidden_actions entry {entry!r} is not a valid kind"
                )
    assert not violations, "policy invariant violations:\n" + "\n".join(violations)


def test_available_capabilities_blocks_are_not_redundant_boilerplate() -> None:
    """Guard the trimmed capability convention.

    With the three-state ``available_capabilities`` model, omitting the block
    inherits the production default (every planner tool enabled, matching
    ``ReplSession()``). A block that explicitly disables all three surfaces
    (``slash_commands: []`` + ``cli_commands: []`` + ``synthetic_suites: []``)
    is the old redundant boilerplate this cleanup removed: it adds noise and
    hides the production default. Scenarios should instead omit the block, or
    set a non-empty allowlist for only the surface(s) they need to constrain.
    """
    offenders: list[str] = []
    for case in load_all_scenarios():
        caps = case.scenario.available_capabilities
        if caps.slash_commands == () and caps.cli_commands == () and caps.synthetic_suites == ():
            offenders.append(case.scenario.id)
    assert not offenders, (
        "These scenarios disable all three planner surfaces via an explicit "
        "all-empty available_capabilities block; omit the block to use the "
        "production default (all tools enabled) instead:\n" + "\n".join(offenders)
    )


def test_scenarios_use_tool_actions_not_legacy_fields() -> None:
    violations: list[str] = []
    for scenario_file in sorted(SCENARIOS_DIR.rglob("*.yml")):
        raw = yaml.safe_load(scenario_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        if "executed_actions" in raw or "gathered_tools_contract" in raw:
            violations.append(
                f"{scenario_file.name}: remove executed_actions/gathered_tools_contract; "
                "use tool_actions."
            )
    assert not violations, "legacy routing action fields found:\n" + "\n".join(violations)


def test_gathered_tools_contract_names_are_registered() -> None:
    from tools.registry import clear_tool_registry_cache, get_registered_tools

    clear_tool_registry_cache()
    registered = {tool.name for tool in get_registered_tools()}

    missing: list[str] = []
    for case in load_all_scenarios():
        contract = case.answer.gathered_tools_contract
        if contract is None:
            continue
        names = (
            *contract.must_call_any,
            *contract.must_call_all,
            *contract.must_not_call,
            *contract.must_return_valid_data,
            *contract.must_return_valid_data_any,
        )
        for name in names:
            if name not in registered:
                missing.append(f"{case.scenario.id}: gathered_tools_contract names {name!r}")
    assert not missing, "gathered_tools_contract references unregistered tool names:\n" + "\n".join(
        missing
    )


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


def test_routing_tests_are_fully_colocated() -> None:
    if not LEGACY_ROUTING_TESTS_DIR.exists():
        return
    unexpected = sorted(
        path.name
        for path in LEGACY_ROUTING_TESTS_DIR.glob("test_*.py")
        if path.name not in ALLOWED_LEGACY_TESTS
    )
    assert not unexpected, (
        "Routing tests must be colocated under cli/interactive_shell/routing/tests/. "
        "No routing tests should remain under tests/cli/interactive_shell/routing/: "
        + ", ".join(unexpected)
    )
