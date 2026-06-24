"""Deterministic detection for ``opensre investigate -i <file>`` quick-start input."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
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
    assert (
        opensre_investigate_slash_text(
            'opensre investigate --input-file "tests/fixtures/alert payload.json"'
        )
        == "/investigate tests/fixtures/alert payload.json"
    )


def test_opensre_investigate_without_path_defaults_to_demo_alert() -> None:
    assert opensre_investigate_slash_text("opensre investigate") == "/investigate alert.json"


def test_deterministic_command_text_maps_opensre_investigate() -> None:
    assert (
        deterministic_command_text("opensre investigate -i alert.json") == "/investigate alert.json"
    )


def test_route_input_keeps_opensre_investigate_on_agent_fast_path() -> None:
    decision = route_input("opensre investigate -i alert.json", ReplSession())
    assert decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT
    assert (
        deterministic_command_text("opensre investigate -i alert.json") == "/investigate alert.json"
    )
