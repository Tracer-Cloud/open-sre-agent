"""Guardrails for canonical routing datasets and test hygiene."""

from __future__ import annotations

import ast
from pathlib import Path

from app.cli.interactive_shell.commands import SLASH_COMMANDS
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    default_target_surface,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    list_rds_postgres_scenarios,
)
from app.cli.interactive_shell.routing.tests._dataset_schema import (
    load_yaml_dataset,
    validate_prompt_dataset,
)

TESTS_DIR = Path(__file__).resolve().parent
DETERMINISTIC_ROUTING_TEST = TESTS_DIR / "test_router_contracts.py"
ACTION_CONTRACTS_TEST = TESTS_DIR / "test_router_action_contracts.py"
LIVE_ROUTING_TEST = TESTS_DIR / "test_router_live_prompts.py"
LIVE_ACTION_ORACLES_TEST = TESTS_DIR / "test_router_live_action_oracles.py"

ROUTER_CONTRACTS_DATASET = "router_contracts.yml"
ROUTER_ACTION_CONTRACTS_DATASET = "router_action_contracts.yml"
ROUTER_LIVE_PROMPTS_DATASET = "router_live_prompts.yml"
ROUTER_LIVE_ACTION_ORACLES_DATASET = "router_live_action_oracles.yml"
_VALID_ACTION_KINDS = {
    "llm_provider",
    "slash",
    "shell",
    "sample_alert",
    "investigation",
    "synthetic_test",
    "task_cancel",
    "cli_command",
    "implementation",
}
_VALID_ACTION_SOURCES = {"deterministic", "llm"}
_VALID_TARGET_SURFACES = {"slash", "terminal", "investigation", "implementation"}


def _extract_loaded_prompt_fixtures(module_path: Path) -> set[str]:
    """Extract literal fixture filenames passed to dataset loader helpers."""
    source = module_path.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(module_path))
    loaded_filenames: set[str] = set()

    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {
            "_load_prompt_cases",
            "_load_contract_cases",
            "_load_action_cases",
            "_load_oracle_cases",
        }:
            continue
        if not node.args:
            continue

        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            loaded_filenames.add(first_arg.value)

    return loaded_filenames


def _mock_policy_violations(module_path: Path) -> list[str]:
    """Detect banned mock usage via syntax tree analysis."""
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


def test_router_contracts_dataset_schema() -> None:
    dataset = load_yaml_dataset(ROUTER_CONTRACTS_DATASET)
    validate_prompt_dataset(
        dataset,
        dataset_name=ROUTER_CONTRACTS_DATASET,
        required_fields=(
            "id",
            "input",
            "expected_kind",
            "expected_signals",
            "expected_command_text",
        ),
        non_empty_string_fields=("id", "input", "expected_kind"),
    )


def test_router_action_contracts_dataset_schema() -> None:
    dataset = load_yaml_dataset(ROUTER_ACTION_CONTRACTS_DATASET)
    validate_prompt_dataset(
        dataset,
        dataset_name=ROUTER_ACTION_CONTRACTS_DATASET,
        required_fields=(
            "id",
            "input",
            "expected_kind",
            "expected_actions",
            "expected_unhandled",
        ),
        non_empty_string_fields=("id", "input", "expected_kind"),
    )


def test_router_action_contracts_action_shapes() -> None:
    dataset = load_yaml_dataset(ROUTER_ACTION_CONTRACTS_DATASET)
    violations: list[str] = []

    for row_idx, row in enumerate(dataset):
        row_id = str(row.get("id", f"row-{row_idx}"))
        raw_actions = row.get("expected_actions", [])
        if not isinstance(raw_actions, list):
            violations.append(f"{row_id}: expected_actions must be a list")
            continue

        for action_idx, action in enumerate(raw_actions):
            prefix = f"{row_id}: expected_actions[{action_idx}]"
            if not isinstance(action, dict):
                violations.append(f"{prefix} must be a mapping")
                continue

            kind = str(action.get("kind", "")).strip()
            content = str(action.get("content", "")).strip()
            source = str(action.get("source", "")).strip()
            target_surface = str(action.get("target_surface", "")).strip()

            if kind not in _VALID_ACTION_KINDS:
                violations.append(f"{prefix} has invalid kind {kind!r}")
            if not content:
                violations.append(f"{prefix} has empty content")
            if source not in _VALID_ACTION_SOURCES:
                violations.append(f"{prefix} has invalid source {source!r}")
            if target_surface not in _VALID_TARGET_SURFACES:
                violations.append(f"{prefix} has invalid target_surface {target_surface!r}")

            if kind == "slash":
                command = str(action.get("command", "")).strip()
                args = action.get("args")
                if command not in SLASH_COMMANDS:
                    violations.append(f"{prefix} has unknown slash command {command!r}")
                if not isinstance(args, list) or not all(
                    isinstance(arg, str) and arg.strip() for arg in args
                ):
                    violations.append(f"{prefix} must define args as a string list")
            elif kind == "synthetic_test":
                suite = str(action.get("suite", "")).strip()
                scenario = str(action.get("scenario", "")).strip()
                if not suite or not scenario:
                    violations.append(f"{prefix} must define suite and scenario")
            elif kind == "cli_command":
                payload = str(action.get("payload", "")).strip()
                if not payload:
                    violations.append(f"{prefix} must define payload")
                if payload.lower().startswith("opensre "):
                    violations.append(f"{prefix} payload must not include opensre prefix")

    assert not violations, "router action contract fixture violations:\n" + "\n".join(violations)


def test_router_live_prompts_dataset_schema() -> None:
    dataset = load_yaml_dataset(ROUTER_LIVE_PROMPTS_DATASET)
    validate_prompt_dataset(
        dataset,
        dataset_name=ROUTER_LIVE_PROMPTS_DATASET,
        required_fields=("id", "input", "expected_kind"),
        non_empty_string_fields=("id", "input", "expected_kind"),
    )


def test_router_live_action_oracles_dataset_schema() -> None:
    dataset = load_yaml_dataset(ROUTER_LIVE_ACTION_ORACLES_DATASET)
    validate_prompt_dataset(
        dataset,
        dataset_name=ROUTER_LIVE_ACTION_ORACLES_DATASET,
        required_fields=(
            "id",
            "input",
            "expected",
        ),
        non_empty_string_fields=("id", "input"),
    )

    violations: list[str] = []
    available_scenarios = set(list_rds_postgres_scenarios())
    for row_idx, row in enumerate(dataset):
        row_id = str(row.get("id", f"row-{row_idx}"))
        tier = str(row.get("tier", "critical")).strip()
        if tier not in {"critical", "full"}:
            violations.append(f"{row_id}: tier must be 'critical' or 'full'")

        runs = row.get("runs", 1)
        if not isinstance(runs, int) or runs < 1:
            violations.append(f"{row_id}: runs must be a positive integer")

        expected = row.get("expected")
        if not isinstance(expected, dict):
            violations.append(f"{row_id}: expected must be a mapping")
            continue

        should_execute = expected.get("should_execute")
        has_unhandled_clause = expected.get("has_unhandled_clause")
        actions = expected.get("actions")
        response_contract = expected.get("response_contract", {})

        if not isinstance(should_execute, bool):
            violations.append(f"{row_id}: expected.should_execute must be a bool")
            continue
        if not isinstance(has_unhandled_clause, bool):
            violations.append(f"{row_id}: expected.has_unhandled_clause must be a bool")
            continue
        if not isinstance(actions, list):
            violations.append(f"{row_id}: expected.actions must be a list")
            continue
        if not isinstance(response_contract, dict):
            violations.append(f"{row_id}: expected.response_contract must be a mapping")
            continue

        if should_execute is False and actions:
            violations.append(f"{row_id}: should_execute=false requires actions=[]")
        if has_unhandled_clause and should_execute:
            violations.append(f"{row_id}: has_unhandled_clause=true requires should_execute=false")

        must_not_contain = response_contract.get("must_not_contain", [])
        if should_execute is False:
            if not isinstance(must_not_contain, list) or "$ /" not in must_not_contain:
                violations.append(
                    f"{row_id}: non-executing cases must include '$ /' in must_not_contain"
                )

        for action_idx, action in enumerate(actions):
            prefix = f"{row_id}: expected.actions[{action_idx}]"
            if not isinstance(action, dict):
                violations.append(f"{prefix} must be a mapping")
                continue

            kind = str(action.get("kind", "")).strip()
            source = str(action.get("source", "")).strip()
            target_surface = str(action.get("target_surface", "")).strip()

            if kind not in _VALID_ACTION_KINDS:
                violations.append(f"{prefix} has invalid kind {kind!r}")
                continue
            if source not in _VALID_ACTION_SOURCES:
                violations.append(f"{prefix} has invalid source {source!r}")
            canonical_surface = default_target_surface(kind)  # type: ignore[arg-type]
            if target_surface != canonical_surface:
                violations.append(
                    f"{prefix} target_surface {target_surface!r} must be {canonical_surface!r}"
                )

            if kind == "slash":
                command = str(action.get("command", "")).strip()
                args = action.get("args")
                if command not in SLASH_COMMANDS:
                    violations.append(f"{prefix} has unknown slash command {command!r}")
                if not isinstance(args, list) or not all(
                    isinstance(arg, str) and arg.strip() for arg in args
                ):
                    violations.append(f"{prefix} must define args as a non-empty string list")
            elif kind == "synthetic_test":
                suite = str(action.get("suite", "")).strip()
                scenario = str(action.get("scenario", "")).strip()
                if not suite or not scenario:
                    violations.append(f"{prefix} synthetic_test requires suite and scenario")
                if scenario and scenario not in available_scenarios:
                    violations.append(f"{prefix} unknown synthetic scenario {scenario!r}")
            elif kind == "cli_command":
                payload = str(action.get("payload", "")).strip()
                if not payload:
                    violations.append(f"{prefix} cli_command requires payload")
                if payload.lower().startswith("opensre "):
                    violations.append(f"{prefix} cli_command payload must not start with opensre")

    assert not violations, (
        "router live action oracle fixture violations:\n" + "\n".join(violations)
    )


def test_deterministic_and_live_routing_tests_do_not_cross_load_datasets() -> None:
    deterministic_modules = (DETERMINISTIC_ROUTING_TEST, ACTION_CONTRACTS_TEST)
    for module in deterministic_modules:
        deterministic_fixtures = _extract_loaded_prompt_fixtures(module)
        assert ROUTER_LIVE_PROMPTS_DATASET not in deterministic_fixtures, (
            f"{module.name} must not load {ROUTER_LIVE_PROMPTS_DATASET!r}."
        )

    live_fixtures = _extract_loaded_prompt_fixtures(LIVE_ROUTING_TEST)
    assert ROUTER_CONTRACTS_DATASET not in live_fixtures, (
        f"{LIVE_ROUTING_TEST.name} must not load {ROUTER_CONTRACTS_DATASET!r}."
    )
    assert ROUTER_ACTION_CONTRACTS_DATASET not in live_fixtures, (
        f"{LIVE_ROUTING_TEST.name} must not load {ROUTER_ACTION_CONTRACTS_DATASET!r}."
    )

    live_action_oracle_fixtures = _extract_loaded_prompt_fixtures(LIVE_ACTION_ORACLES_TEST)
    assert live_action_oracle_fixtures == {ROUTER_LIVE_ACTION_ORACLES_DATASET}


def test_routing_test_modules_do_not_use_mock_patterns() -> None:
    violations: list[str] = []

    guarded_test_modules = (
        TESTS_DIR / "test_router_contracts.py",
        TESTS_DIR / "test_router_action_contracts.py",
        TESTS_DIR / "test_router_live_prompts.py",
        TESTS_DIR / "test_router_live_action_oracles.py",
    )

    for test_path in guarded_test_modules:
        if not test_path.exists():
            continue
        for violation in _mock_policy_violations(test_path):
            violations.append(f"{test_path.name}: found disallowed {violation}")

    assert not violations, (
        "No-mocks policy violated in routing tests. "
        "Remove mock usage from canonical routing suites.\n" + "\n".join(violations)
    )
