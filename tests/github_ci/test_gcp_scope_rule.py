"""``make test-scope`` must route a GCP change to the GCP tests.

Before this rule, ``integrations/gcp/**`` fell through to the
``PathRule("integrations/", ("tests/integrations/",))`` catch-all, which runs
``tests/integrations/`` and nothing under ``tests/tools/``. So a change to a GCP
tool's schema, surfaces or telemetry registration ran none of the tests that
assert on them, and the first red was whatever full-suite run happened next.

``classify`` breaks on the **first** matching prefix, so this is also an
ordering guard: moving the GCP rule below the ``integrations/`` catch-all makes
it unreachable without changing either rule's contents.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_REPO_ROOT / ".github" / "ci"))
from test_scope_rules import classify  # type: ignore[import-not-found]  # noqa: E402

#: Tests that assert on GCP tool metadata and live outside ``tests/integrations/``,
#: which is what the catch-all would have given us.
_REQUIRED_TARGETS = (
    "tests/tools/test_gcp_tools.py",
    "tests/tools/test_gcp_metrics_scope.py",
    "tests/tools/test_gcp_alerting.py",
    "tests/tools/test_infra_tools_on_action_surface.py",
    "tests/tools/test_telemetry.py",
    "tests/tools/test_registry_index.py",
)


@pytest.mark.parametrize(
    "changed",
    [
        "integrations/gcp/tool_params.py",
        "integrations/gcp/tools/gcp_logging_query_tool/__init__.py",
        "integrations/gcp/tools/gcp_monitoring_query_tool/__init__.py",
        "integrations/gcp/projects.py",
    ],
)
def test_gcp_paths_route_gcp_tests(changed: str) -> None:
    _escalate, targets, _areas = classify([changed])
    targets = set(targets)

    missing = [target for target in _REQUIRED_TARGETS if target not in targets]
    assert not missing, f"{changed} does not route: {missing}"


def test_every_routed_target_actually_exists() -> None:
    """A typo'd target silently narrows the scope instead of failing."""
    _escalate, targets, _areas = classify(["integrations/gcp/tool_params.py"])

    for target in targets:
        assert (_REPO_ROOT / target).exists(), f"routed to a non-existent path: {target}"


def test_the_gcp_rule_is_ordered_before_the_integrations_catch_all() -> None:
    """``classify`` breaks on first match, so order is the whole contract."""
    from test_scope_rules import RULES

    prefixes = [rule.path_prefix for rule in RULES]

    assert "integrations/gcp/" in prefixes, "the GCP rule is gone"
    assert prefixes.index("integrations/gcp/") < prefixes.index("integrations/")
