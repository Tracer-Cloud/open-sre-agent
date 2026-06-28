"""Guardrails for turn scenario directories and test hygiene."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import pytest
import yaml

from interactive_shell.tools.tool_registry import (
    REGISTRY,
    TOOL_KIND_TO_NAME,
    ToolKind,
)
from tests.core.agent.scenario_loader import (
    DEFAULT_GATE_PER_CLASS,
    INTENT_TO_BEHAVIOR_CLASS,
    SCENARIOS_DIR,
    SelectionSpec,
    effective_runs,
    is_full_selection,
    load_all_scenarios,
    max_runs_cap,
    parse_selection_spec,
    scenario_complexity,
    select_cases,
    select_representative,
    validate_action_shape,
)

TESTS_DIR = Path(__file__).resolve().parent
TURN_SCENARIOS_TEST = TESTS_DIR / "test_turn_scenarios.py"


def _repo_root() -> Path:
    for parent in TESTS_DIR.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return TESTS_DIR.parents[2]


LEGACY_TURN_TESTS_DIRS = (_repo_root() / "tests" / "interactive_shell" / "harness",)
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
            tool_name = TOOL_KIND_TO_NAME.get(cast(ToolKind, kind))
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
        from tests.core.agent.scenario_loader import VALID_ACTION_KINDS

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
    assert not violations, "legacy action fields found:\n" + "\n".join(violations)


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


def test_turn_test_modules_do_not_use_mock_patterns() -> None:
    violations: list[str] = []
    for test_path in (TURN_SCENARIOS_TEST, ORACLE_RUNTIME):
        if not test_path.exists():
            continue
        for violation in _mock_policy_violations(test_path):
            violations.append(f"{test_path.name}: found disallowed {violation}")
    assert not violations, (
        "No-mocks policy violated in turn tests. "
        "Remove mock usage from canonical turn suites.\n" + "\n".join(violations)
    )


def test_parse_selection_spec_variants() -> None:
    assert parse_selection_spec(None) is None
    assert parse_selection_spec("") is None
    assert parse_selection_spec("   ") is None
    assert parse_selection_spec("complex:5") == SelectionSpec(mode="complex", count=5)
    assert parse_selection_spec("sample:10%") == SelectionSpec(mode="sample", fraction=0.1)
    assert parse_selection_spec("complex:0.25") == SelectionSpec(mode="complex", fraction=0.25)
    # A bare mode defaults to the 5% fast slice.
    assert parse_selection_spec("complex") == SelectionSpec(mode="complex", fraction=0.05)


def test_parse_selection_spec_rejects_bad_input() -> None:
    for bad in ("bogus:5", "complex:0", "sample:-1", "sample:101%", "sample:0%"):
        with pytest.raises(ValueError):
            parse_selection_spec(bad)


def test_select_cases_none_returns_all() -> None:
    cases = load_all_scenarios()
    assert select_cases(cases, spec=None) == cases


def test_select_cases_count_preserves_order_and_is_deterministic() -> None:
    cases = load_all_scenarios()
    first = select_cases(cases, spec="sample:3", seed=7)
    second = select_cases(cases, spec="sample:3", seed=7)
    assert len(first) == 3
    assert [c.scenario.id for c in first] == [c.scenario.id for c in second]
    order = {c.scenario.id: i for i, c in enumerate(cases)}
    selected_positions = [order[c.scenario.id] for c in first]
    assert selected_positions == sorted(selected_positions)


def test_select_cases_complex_picks_the_top_scored() -> None:
    cases = load_all_scenarios()
    selected = select_cases(cases, spec="complex:5")
    assert len(selected) == 5
    selected_ids = {c.scenario.id for c in selected}
    selected_scores = [scenario_complexity(c) for c in selected]
    other_scores = [scenario_complexity(c) for c in cases if c.scenario.id not in selected_ids]
    assert min(selected_scores) >= max(other_scores)


def test_select_cases_percentage_rounds_up() -> None:
    cases = load_all_scenarios()
    # 5% of 61 scenarios rounds up to 4.
    assert len(select_cases(cases, spec="complex:5%")) == 4


def test_select_representative_covers_every_behavior_class() -> None:
    cases = load_all_scenarios()
    selected = select_representative(cases)
    by_class: dict[str, int] = {}
    for case in selected:
        by_class[case.scenario.behavior_class] = by_class.get(case.scenario.behavior_class, 0) + 1
    all_classes = {case.scenario.behavior_class for case in cases}
    # Every class is represented, capped at DEFAULT_GATE_PER_CLASS per class.
    assert set(by_class) == all_classes
    assert all(count <= DEFAULT_GATE_PER_CLASS for count in by_class.values())
    # The gate is a strict, much smaller subset of the full suite.
    assert 0 < len(selected) < len(cases)


def test_select_representative_is_deterministic_and_order_preserving() -> None:
    cases = load_all_scenarios()
    first = select_representative(cases)
    second = select_representative(cases)
    assert [c.scenario.id for c in first] == [c.scenario.id for c in second]
    order = {c.scenario.id: i for i, c in enumerate(cases)}
    positions = [order[c.scenario.id] for c in first]
    assert positions == sorted(positions)


def test_select_representative_picks_most_complex_within_class() -> None:
    cases = load_all_scenarios()
    selected_ids = {c.scenario.id for c in select_representative(cases, per_class=1)}
    by_class: dict[str, list] = {}
    for case in cases:
        by_class.setdefault(case.scenario.behavior_class, []).append(case)
    for behavior_class, class_cases in by_class.items():
        chosen = [c for c in class_cases if c.scenario.id in selected_ids]
        assert len(chosen) == 1, behavior_class
        top = max(class_cases, key=lambda c: (scenario_complexity(c), c.scenario.id))
        assert chosen[0].scenario.id == top.scenario.id


def test_is_full_selection_recognizes_full_aliases() -> None:
    for spec in ("all", "ALL", "full", "everything", "*", "  all  "):
        assert is_full_selection(spec)
    for spec in (None, "", "complex:5", "sample:3"):
        assert not is_full_selection(spec)


def test_max_runs_cap_defaults_to_one_and_honors_uncapped_tokens() -> None:
    assert max_runs_cap(None) == 1
    assert max_runs_cap("3") == 3
    for uncapped in ("0", "all", "off", "none", ""):
        assert max_runs_cap(uncapped) is None


def test_effective_runs_caps_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TURN_MAX_RUNS", raising=False)
    # Default cap of 1 collapses majority voting to a single run.
    assert effective_runs(5) == 1
    assert effective_runs(1) == 1
    monkeypatch.setenv("TURN_MAX_RUNS", "0")
    # Uncapped honours the fixture runs.
    assert effective_runs(5) == 5
    monkeypatch.setenv("TURN_MAX_RUNS", "3")
    assert effective_runs(5) == 3
    assert effective_runs(2) == 2


def test_scenario_complexity_ranks_compound_above_chat_handoff() -> None:
    cases = {case.scenario.intent_class: case for case in load_all_scenarios()}
    compound = cases.get("compound")
    chat = cases.get("chat_handoff")
    assert compound is not None
    assert chat is not None
    assert scenario_complexity(compound) > scenario_complexity(chat)


def test_turn_tests_are_fully_colocated() -> None:
    unexpected = sorted(
        f"{path.parent.relative_to(_repo_root()).as_posix()}/{path.name}"
        for directory in LEGACY_TURN_TESTS_DIRS
        if directory.exists()
        for path in directory.glob("test_*.py")
        if path.name not in ALLOWED_LEGACY_TESTS
    )
    assert not unexpected, (
        "Turn tests must be colocated under tests/core/agent/. "
        "No turn tests should remain under split legacy test directories: " + ", ".join(unexpected)
    )
