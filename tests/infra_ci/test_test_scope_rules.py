"""Unit tests for branch-scoped test path mapping."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parents[2] / "infra" / "ci" / "test_scope_rules.py"


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


def test_interactive_shell_routes_to_its_own_tests() -> None:
    rules = _rules_module()
    escalate, targets, _ = rules.classify(["interactive_shell/runtime/session.py"])
    assert not escalate
    assert targets == ["tests/interactive_shell/"]


def test_three_areas_escalates() -> None:
    rules = _rules_module()
    changed = [
        "tools/a.py",
        "cli/b.py",
        "integrations/hermes/c.py",
    ]
    escalate, _, areas = rules.classify(changed)
    assert escalate
    assert len(areas) == 3


def test_pipeline_always_escalates() -> None:
    rules = _rules_module()
    escalate, _, _ = rules.classify(["core/orchestration/entrypoints.py"])
    assert escalate


def test_changed_test_file_is_targeted() -> None:
    rules = _rules_module()
    path = "tests/infra_ci/test_test_scope_rules.py"
    escalate, targets, _ = rules.classify([path])
    assert not escalate
    assert targets == [path]
