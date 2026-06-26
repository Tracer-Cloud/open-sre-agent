"""Metamorphic invariants for deterministic command-text detection."""

from __future__ import annotations

import pytest

from interactive_shell.harness.orchestration.command_dispatch import (
    deterministic_command_text,
)


@pytest.mark.parametrize(
    "prompt",
    [
        "help",
        " HELP ",
        "\thelp\t",
    ],
)
def test_help_alias_variants_normalize_to_slash_help(prompt: str) -> None:
    assert deterministic_command_text(prompt) == "/help"


@pytest.mark.parametrize(
    "prompt",
    [
        "opensre investigate -i alert.json",
        "  OPENSRE investigate -i alert.json  ",
        "\topensre   investigate   -i   alert.json\t",
    ],
)
def test_opensre_investigate_variants_normalize_deterministically(prompt: str) -> None:
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
    assert deterministic_command_text(prompt) is None
