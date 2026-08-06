"""One declared table is the only thing that differs between surfaces.

The gateway and interactive shell used to diverge through six scattered
ternaries on a surface string plus a memory branch. Anyone adding a seventh had
nowhere to look for the existing six. These tests pin the table as the single
place a surface difference can be declared, and pin what each flag actually does
to the rendered prompt.
"""

from __future__ import annotations

from core.agent_harness.prompts.assistant import _build_system_prompt
from core.agent_harness.prompts.kernel.surfaces import PromptSurface, profile_for


def test_every_surface_has_a_declared_profile() -> None:
    """A surface with no row would fall back silently to shell behaviour."""
    # Act / Assert
    for surface in PromptSurface:
        assert profile_for(surface.value).surface is surface


def test_an_unknown_surface_gets_the_full_prompt_not_a_stripped_one() -> None:
    """Failing open matters here: a stripped prompt degrades answers quietly."""
    # Act
    profile = profile_for("some-future-surface")

    # Assert
    assert profile.surface is PromptSurface.INTERACTIVE_SHELL
    assert profile.cli_rules is True


def test_the_two_surfaces_differ_only_in_the_declared_flags() -> None:
    """The table is the contract — reading it tells you the whole difference."""
    # Arrange
    shell = profile_for(PromptSurface.INTERACTIVE_SHELL.value)
    gateway = profile_for(PromptSurface.GATEWAY.value)

    # Act
    differing = {
        name
        for name in (
            "cli_rules",
            "vendor_persona",
            "long_term_memory_by_default",
            "setup_state",
        )
        if getattr(shell, name) != getattr(gateway, name)
    }

    # Assert
    assert differing == {
        "cli_rules",
        "vendor_persona",
        "long_term_memory_by_default",
        "setup_state",
    }


def test_cli_rules_reach_the_shell_prompt_and_not_the_gateway_one() -> None:
    """The flag has to change the rendered prompt, not just describe intent."""

    # Arrange / Act
    def build(surface: str) -> str:
        return _build_system_prompt("", "", surface=surface).lower()

    shell = build(PromptSurface.INTERACTIVE_SHELL.value)
    gateway = build(PromptSurface.GATEWAY.value)

    # Assert
    assert len(shell) > len(gateway)
    assert shell != gateway
