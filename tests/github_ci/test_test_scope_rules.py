"""Unit tests for branch-scoped test path mapping."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parents[2] / ".github" / "ci" / "test_scope_rules.py"


def _rules_module():
    name = "test_scope_rules"
    spec = importlib.util.spec_from_file_location(name, _RULES_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_llm_cli_rule_takes_priority_over_integrations() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["integrations/llm_cli/foo.py"])
    assert not escalate
    assert targets == ["tests/integrations/llm_cli/"]


def test_hermes_rule_routes_to_tests_hermes_not_integrations() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["integrations/hermes/classifier.py"])
    assert not escalate
    assert targets == ["tests/hermes/"]


def test_grafana_rule_includes_integration_and_tool_tests() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["integrations/grafana/tools/__init__.py"])
    assert not escalate
    assert "tests/integrations/grafana/" in targets
    assert "tests/tools/test_grafana_logs_tool.py" in targets
    assert "tests/e2e/grafana_validation/" in targets
    assert len(targets) == 8


def test_datadog_rule_includes_integration_and_tool_tests() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["integrations/datadog/tools/__init__.py"])
    assert not escalate
    assert "tests/integrations/datadog/" in targets
    assert "tests/tools/test_datadog_logs_tool.py" in targets
    assert len(targets) == 7


def test_interactive_shell_surface_routes_to_its_own_tests() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["surfaces/interactive_shell/controller.py"])
    assert not escalate
    assert targets == ["tests/interactive_shell/"]


def test_surfaces_cli_routes_to_cli_tests() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["surfaces/cli/wizard/flow.py"])
    assert not escalate
    assert targets == ["tests/cli/"]


def test_gateway_routes_to_package_local_tests() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["gateway/start_gateway.py"])
    assert not escalate
    assert targets == ["gateway/tests/"]


def test_three_areas_escalates() -> None:
    rules = _rules_module()
    changed = [
        "tools/a.py",
        "surfaces/cli/b.py",
        "integrations/hermes/c.py",
    ]
    escalate, _, areas = rules.classify(changed)
    assert escalate
    assert len(areas) == 3


def test_pipeline_always_escalates() -> None:
    rules = _rules_module()
    escalate, _, _ = rules.classify(["tools/investigation/capability.py"])
    assert escalate


def test_changed_test_file_is_targeted() -> None:
    rules = _rules_module()
    path = "tests/github_ci/test_test_scope_rules.py"
    escalate, targets, _ = rules.classify([path])
    assert not escalate
    assert targets == [path]


def test_reporting_rule_routes_to_tests_delivery() -> None:
    # classify() drops targets that don't exist on disk, so make the dependency
    # explicit: a missing dir here means "recreate it or update the rule", not
    # "the routing changed".
    assert Path("tests/delivery/").is_dir(), (
        "tests/delivery/ missing — update the rule or recreate the dir"
    )
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["tools/investigation/reporting/publish.py"])
    assert not escalate
    assert targets == ["tests/delivery/"]


def test_all_rule_targets_are_tuples_not_bare_strings() -> None:
    # A single target written as ("x") is a str, not a tuple — classify() would
    # then iterate it character by character. .github/ci is outside the mypy paths
    # so this footgun isn't caught by typecheck; guard it here for every rule.
    rules = _rules_module()
    for rule in rules.RULES:
        assert isinstance(rule.test_targets, tuple), rule.path_prefix
