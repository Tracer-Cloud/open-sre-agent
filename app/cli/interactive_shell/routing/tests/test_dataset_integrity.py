"""Guardrails for canonical routing datasets and test hygiene."""

from __future__ import annotations

import ast
from pathlib import Path

from app.cli.interactive_shell.routing.tests._dataset_schema import (
    load_yaml_dataset,
    validate_prompt_dataset,
)

TESTS_DIR = Path(__file__).resolve().parent
DETERMINISTIC_ROUTING_TEST = TESTS_DIR / "test_router_contracts.py"
LIVE_ROUTING_TEST = TESTS_DIR / "test_router_live_prompts.py"

ROUTER_CONTRACTS_DATASET = "router_contracts.yml"
ROUTER_LIVE_PROMPTS_DATASET = "router_live_prompts.yml"


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
        if node.func.id not in {"_load_prompt_cases", "_load_contract_cases"}:
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


def test_router_live_prompts_dataset_schema() -> None:
    dataset = load_yaml_dataset(ROUTER_LIVE_PROMPTS_DATASET)
    validate_prompt_dataset(
        dataset,
        dataset_name=ROUTER_LIVE_PROMPTS_DATASET,
        required_fields=("id", "input", "expected_kind"),
        non_empty_string_fields=("id", "input", "expected_kind"),
    )


def test_deterministic_and_live_routing_tests_do_not_cross_load_datasets() -> None:
    deterministic_fixtures = _extract_loaded_prompt_fixtures(DETERMINISTIC_ROUTING_TEST)
    live_fixtures = _extract_loaded_prompt_fixtures(LIVE_ROUTING_TEST)

    assert ROUTER_LIVE_PROMPTS_DATASET not in deterministic_fixtures, (
        f"{DETERMINISTIC_ROUTING_TEST.name} must not load {ROUTER_LIVE_PROMPTS_DATASET!r}."
    )
    assert ROUTER_CONTRACTS_DATASET not in live_fixtures, (
        f"{LIVE_ROUTING_TEST.name} must not load {ROUTER_CONTRACTS_DATASET!r}."
    )


def test_routing_test_modules_do_not_use_mock_patterns() -> None:
    violations: list[str] = []

    guarded_test_modules = (
        TESTS_DIR / "test_router_contracts.py",
        TESTS_DIR / "test_router_live_prompts.py",
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
