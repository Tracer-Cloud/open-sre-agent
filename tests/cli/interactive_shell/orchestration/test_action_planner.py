"""Unit tests for the action planner facade."""

from __future__ import annotations

import pytest

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper as action_planner_module


def test_plan_cli_actions_health_and_list() -> None:
    msg = "check opensre health and show connected services"
    assert action_planner_module.plan_cli_actions(msg) == ["/health", "/list integrations"]


def test_plan_actions_with_unhandled_all_handled() -> None:
    msg = "check opensre health and show connected services"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)
    assert not unhandled
    assert [a.kind for a in actions] == ["slash", "slash"]


def test_plan_terminal_tasks_returns_kinds() -> None:
    msg = "check opensre health and show connected services"
    assert action_planner_module.plan_terminal_tasks(msg) == ["slash", "slash"]


def test_plan_synthetic_test_without_scenario_uses_default() -> None:
    msg = "run a single synthetic test"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:001-replication-lag")
    ]


def test_plan_synthetic_test_with_explicit_scenario_id() -> None:
    msg = "run synthetic test 005-failover"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:005-failover")
    ]
    assert action_planner_module.plan_terminal_tasks(msg) == ["synthetic_test"]
    assert action_planner_module.plan_cli_actions(msg) == []


def test_plan_typoed_synthetic_test_with_explicit_scenario_id() -> None:
    msg = "rnu syntehtic tset 002-connection-exhaustion"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:002-connection-exhaustion")
    ]
    assert action_planner_module.plan_terminal_tasks(msg) == ["synthetic_test"]
    assert action_planner_module.plan_cli_actions(msg) == []


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic scenario resolution — deterministic paths
#
# Canonical full IDs ("005-failover") are matched by regex. Bare numbers
# ("999") that don't map to any known scenario emit SYNTHETIC_UNKNOWN_PREFIX.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def _clear_scenario_cache() -> None:
    """Drop the lru_cache so each test sees a fresh scenario list snapshot."""
    action_planner_module.list_rds_postgres_scenarios.cache_clear()


def test_plan_synthetic_test_unknown_numeric_id_emits_unknown_sentinel(
    _clear_scenario_cache: None,
) -> None:
    """A user-specified numeric ID with no matching scenario surfaces an error.

    Regression: previously this silently fell back to ``DEFAULT_SYNTHETIC_SCENARIO``
    (``001-replication-lag``), so asking to run ``test 999`` actually ran
    ``001-replication-lag`` without telling the user. Now the planner emits a
    ``SYNTHETIC_UNKNOWN_PREFIX`` sentinel and the executor reports the mismatch.
    """
    msg = "run synthetic test 999"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", f"{action_planner_module.SYNTHETIC_UNKNOWN_PREFIX}999")
    ]


def test_plan_synthetic_test_without_numeric_hint_still_falls_back_to_default(
    _clear_scenario_cache: None,
) -> None:
    """A bare request without any scenario hint keeps the convenience default.

    "run a single synthetic test" carries no specific intent, so falling back
    to ``DEFAULT_SYNTHETIC_SCENARIO`` is still the right UX. The unknown-sentinel
    path is reserved for user-specified IDs that genuinely don't exist.
    """
    msg = "run a single synthetic test"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:001-replication-lag")
    ]


def test_plan_synthetic_test_full_id_matches_deterministically(
    _clear_scenario_cache: None,
) -> None:
    """A canonical full scenario slug is matched by regex without any LLM call."""
    msg = "run synthetic test 003-storage-full"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:003-storage-full")
    ]


def test_plan_synthetic_test_bare_numeric_id_resolves_to_matching_scenario(
    _clear_scenario_cache: None,
) -> None:
    """A bare number that matches a known scenario prefix resolves to that scenario.

    Regression: "run synthetic test 005 now" was silently routing to
    DEFAULT_SYNTHETIC_SCENARIO (001-replication-lag) because _detect_unresolved_numeric_hint
    returned None (the hint WAS resolved), but the resolved scenario was never used —
    the code fell straight through to the default fallback.
    """
    msg = "run synthetic test 005 now"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:005-failover")
    ]


def test_plan_terminal_tasks_returns_implementation_action() -> None:
    msg = "please implement process auto-discovery"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("implementation", "process auto-discovery")]
    assert action_planner_module.plan_terminal_tasks(msg) == ["implementation"]
    assert action_planner_module.plan_cli_actions(msg) == []


def test_plan_task_cancel_before_shell_kill() -> None:
    msg = "kill the syntehtic_test because it is running way too long"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("task_cancel", "synthetic_test")]
    assert action_planner_module.plan_terminal_tasks(msg) == ["task_cancel"]
    assert action_planner_module.plan_cli_actions(msg) == []


def test_stop_process_prompt_is_not_task_cancel() -> None:
    msg = "stop the process of auto-investigation and give me a manual runbook"
    actions, unhandled = action_planner_module.plan_actions_with_unhandled(msg)

    assert actions == []
    assert unhandled is True


def test_plan_cli_actions_remote_deployment_inventory_questions() -> None:
    messages = (
        "Which remote deployments are connected?",
        "Which remote's deployments are connected?",
        "What remote deployments are connected?",
        "show remote deployments",
        "list remote deployments",
    )

    for message in messages:
        assert action_planner_module.plan_cli_actions(message) == ["/remote"]
