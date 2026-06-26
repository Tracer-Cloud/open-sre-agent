"""Routing execution tests using typed fakes instead of monkeypatch-heavy seams."""

from __future__ import annotations

from rich.console import Console

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
    execute_cli_actions,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.terminal_actions.models import (
    ActionPlanningDecision,
)
from cli.interactive_shell.routing.tests.orchestration.routing_test_harness import (
    FakeDispatcher,
    FakePlanner,
    RoutingHarness,
    planned_action,
)
from cli.interactive_shell.runtime.session import ReplSession


def test_execute_with_harness_dispatches_slash_action() -> None:
    planner = FakePlanner(
        result=ActionPlanningDecision((planned_action("slash", "/health"),), False, ())
    )
    dispatcher = FakeDispatcher()
    harness = RoutingHarness(planner=planner, dispatcher=dispatcher)

    result = execute_cli_actions(
        "check health",
        ReplSession(),
        cast_console(harness.console),
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 0
    assert dispatcher.calls == [("slash_invoke", {"command": "/health", "args": []})]


def test_execute_with_harness_hands_off_handoff_only_plan() -> None:
    planner = FakePlanner(
        result=ActionPlanningDecision(
            (planned_action("assistant_handoff", "docs:help"),),
            True,
            (),
        )
    )
    dispatcher = FakeDispatcher()
    harness = RoutingHarness(planner=planner, dispatcher=dispatcher)

    result = execute_cli_actions(
        "half actionable prompt",
        ReplSession(),
        cast_console(harness.console),
        deps=harness.deps,
    )

    # A handoff-only plan is not "handled" by terminal execution: it falls
    # through to the conversational assistant. v0.1 never denies the turn.
    assert result.handled is False
    assert result.has_unhandled_clause is False
    assert result.planned_count == 0
    assert dispatcher.calls == []


def test_execute_with_harness_handles_planner_unavailable() -> None:
    planner = FakePlanner(result=None)
    dispatcher = FakeDispatcher()
    harness = RoutingHarness(planner=planner, dispatcher=dispatcher)

    result = execute_cli_actions(
        "planner outage",
        ReplSession(),
        cast_console(harness.console),
        deps=harness.deps,
    )

    # Planner outage falls through to the assistant instead of denying.
    assert result.handled is False
    assert result.has_unhandled_clause is False
    assert result.planned_count == 0
    assert dispatcher.calls == []


def cast_console(console: Console) -> Console:
    return console
