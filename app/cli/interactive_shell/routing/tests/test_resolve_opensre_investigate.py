"""Deterministic routing for ``opensre investigate -i <file>`` quick-start input."""

from __future__ import annotations

from app.cli.interactive_shell.routing.resolve_cli_command.evaluator import (
    resolve_cli_command,
)
from app.cli.interactive_shell.routing.resolve_cli_command.matcher import (
    opensre_investigate_slash_text,
)
from app.cli.interactive_shell.routing.router import RouteKind, route_input
from app.cli.interactive_shell.runtime.session import ReplSession


def test_opensre_investigate_slash_text_maps_input_flag() -> None:
    assert (
        opensre_investigate_slash_text("opensre investigate -i alert.json")
        == "/investigate alert.json"
    )
    assert (
        opensre_investigate_slash_text(
            "opensre investigate --input tests/fixtures/openclaw_test_alert.json"
        )
        == "/investigate tests/fixtures/openclaw_test_alert.json"
    )


def test_opensre_investigate_without_path_defaults_to_demo_alert() -> None:
    assert opensre_investigate_slash_text("opensre investigate") == "/investigate alert.json"


def test_resolve_cli_command_routes_opensre_investigate_as_slash() -> None:
    session = ReplSession()
    decision = resolve_cli_command("opensre investigate -i alert.json", session)
    assert decision is not None
    assert decision.route_kind == RouteKind.SLASH
    assert decision.command_text == "/investigate alert.json"
    assert "opensre_investigate" in decision.matched_signals


def test_route_input_does_not_send_opensre_investigate_to_llm_planner() -> None:
    decision = route_input("opensre investigate -i alert.json", ReplSession())
    assert decision.route_kind == RouteKind.SLASH
    assert decision.command_text == "/investigate alert.json"
