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
ROUTING_PACKAGE_DIR = TESTS_DIR.parent
MESSAGE_ROUTE_EVALUATOR = ROUTING_PACKAGE_DIR / "message_route" / "evaluator.py"
LLM_INTENT_CLASSIFIER = (
    ROUTING_PACKAGE_DIR.parent / "orchestration" / "llm_intent_classifier.py"
)
SYNTHETIC_SCENARIO_RESOLVER = (
    ROUTING_PACKAGE_DIR.parent / "orchestration" / "synthetic_scenario_resolver.py"
)

ROUTER_CONTRACTS_DATASET = "router_contracts.yml"
ROUTER_LIVE_PROMPTS_DATASET = "router_live_prompts.yml"
ROUTER_COMPLEX_PROMPTS_DATASET = "router_complex_prompts.yml"


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


def _parse_module(module_path: Path) -> ast.Module:
    source = module_path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(module_path))


def _function_by_name(module: ast.Module, function_name: str) -> ast.FunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    return None


def _cache_clear_violations(module_path: Path, *, clear_function_name: str) -> list[str]:
    module = _parse_module(module_path)
    violations: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "cache_clear":
            continue
        parent_fn: ast.FunctionDef | None = None
        for maybe_parent in ast.walk(module):
            if isinstance(maybe_parent, ast.FunctionDef) and node in ast.walk(maybe_parent):
                parent_fn = maybe_parent
                break
        if parent_fn is None or parent_fn.name != clear_function_name:
            violations.append(
                f"{module_path.name}: cache_clear() only allowed inside {clear_function_name}()"
            )
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


def test_router_complex_prompts_dataset_schema() -> None:
    dataset = load_yaml_dataset(ROUTER_COMPLEX_PROMPTS_DATASET)
    validate_prompt_dataset(
        dataset,
        dataset_name=ROUTER_COMPLEX_PROMPTS_DATASET,
        required_fields=(
            "id",
            "input",
            "expected_kind",
            "expected_unhandled_clause",
            "required_actions",
        ),
        non_empty_string_fields=("id", "input", "expected_kind"),
    )

    for index, record in enumerate(dataset, start=1):
        required_actions = record["required_actions"]
        if not isinstance(required_actions, list) or not required_actions:
            msg = (
                f"{ROUTER_COMPLEX_PROMPTS_DATASET} entry #{index} field "
                "'required_actions' must be a non-empty list."
            )
            raise AssertionError(msg)
        for action_index, action in enumerate(required_actions, start=1):
            if not isinstance(action, dict):
                msg = (
                    f"{ROUTER_COMPLEX_PROMPTS_DATASET} entry #{index} action #{action_index} "
                    "must be a mapping."
                )
                raise AssertionError(msg)
            kind = action.get("kind")
            content = action.get("content")
            if not isinstance(kind, str) or not kind.strip():
                msg = (
                    f"{ROUTER_COMPLEX_PROMPTS_DATASET} entry #{index} action #{action_index} "
                    "must include non-empty string field 'kind'."
                )
                raise AssertionError(msg)
            if kind not in {"remote_deploy", "remote_investigation"}:
                msg = (
                    f"{ROUTER_COMPLEX_PROMPTS_DATASET} entry #{index} action #{action_index} "
                    "field 'kind' must be one of {'remote_deploy', 'remote_investigation'}."
                )
                raise AssertionError(msg)
            if not isinstance(content, str) or not content.strip():
                msg = (
                    f"{ROUTER_COMPLEX_PROMPTS_DATASET} entry #{index} action #{action_index} "
                    "must include non-empty string field 'content'."
                )
                raise AssertionError(msg)


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


def test_message_route_forbids_deterministic_non_command_fallback() -> None:
    module = _parse_module(MESSAGE_ROUTE_EVALUATOR)
    violations: list[str] = []

    banned_modules = {
        "re",
        "app.cli.interactive_shell.intent.intent_parser",
        "app.cli.interactive_shell.routing.command_route",
        "app.cli.interactive_shell.routing.command_route.catalog",
        "app.cli.interactive_shell.routing.command_route.evaluator",
        "app.cli.interactive_shell.routing.command_route.matcher",
        "app.cli.interactive_shell.routing.utils.matching",
    }
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in banned_modules:
                    violations.append(f"banned import: {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module in banned_modules:
            violations.append(f"banned from-import: {node.module}")

    handle_fn = _function_by_name(module, "handle_message_with_agent")
    if handle_fn is None:
        violations.append("missing handle_message_with_agent()")
    else:
        fallback_returns_cli_agent = False
        for node in ast.walk(handle_fn):
            if not isinstance(node, ast.Return):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != "RouteDecision":
                continue
            args = node.value.args
            if (
                len(args) >= 1
                and isinstance(args[0], ast.Attribute)
                and isinstance(args[0].value, ast.Name)
                and args[0].value.id == "RouteKind"
                and args[0].attr == "CLI_AGENT"
            ):
                fallback_returns_cli_agent = True
        if not fallback_returns_cli_agent:
            violations.append("fallback RouteDecision must remain RouteKind.CLI_AGENT")

    assert not violations, (
        "Deterministic non-command fallback guardrail violated in message_route/evaluator.py\n"
        + "\n".join(violations)
    )


def test_llm_caches_do_not_clear_on_single_failure() -> None:
    violations = [
        *_cache_clear_violations(
            LLM_INTENT_CLASSIFIER,
            clear_function_name="clear_classify_cache",
        ),
        *_cache_clear_violations(
            SYNTHETIC_SCENARIO_RESOLVER,
            clear_function_name="clear_resolver_cache",
        ),
    ]
    assert not violations, (
        "LLM cache stability guardrail violated.\n"
        "Do not call cache_clear() on classification/resolution miss paths.\n"
        + "\n".join(violations)
    )
