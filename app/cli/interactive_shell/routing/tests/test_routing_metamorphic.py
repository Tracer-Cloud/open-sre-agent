"""Metamorphic routing invariants for the single-branch agent entrypoint."""

from __future__ import annotations

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
)
from app.cli.interactive_shell.routing.router import RouteKind, route_input
from app.cli.interactive_shell.runtime.session import ReplSession


@pytest.mark.parametrize(
    "prompt",
    [
        "help",
        " HELP ",
        "\thelp\t",
    ],
)
def test_help_alias_variants_dispatch_slash_help(prompt: str) -> None:
    decision = route_input(prompt, ReplSession())
    assert decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT
    assert deterministic_command_text(prompt) == "/help"


@pytest.mark.parametrize(
    "prompt",
    [
        "opensre investigate -i alert.json",
        "  OPENSRE investigate -i alert.json  ",
        "\topensre   investigate   -i   alert.json\t",
    ],
)
def test_opensre_investigate_variants_dispatch_deterministically(prompt: str) -> None:
    decision = route_input(prompt, ReplSession())
    assert decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT
    command_text = deterministic_command_text(prompt)
    assert command_text is not None
    assert command_text.startswith("/investigate")


@pytest.mark.parametrize(
    "prompt",
    [
        "check opensre health and show connected services",
        "CHECK OPENsRE HEALTH and SHOW connected SERVICES",
        "  check opensre health and show connected services  ",
    ],
)
def test_non_command_variants_have_no_deterministic_command(prompt: str) -> None:
    decision = route_input(prompt, ReplSession())
    assert decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT
    assert deterministic_command_text(prompt) is None
