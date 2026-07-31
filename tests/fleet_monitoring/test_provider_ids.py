"""Pins ``FleetAgentProvider`` membership and its string round-trip (#4536)."""

from __future__ import annotations

from tools.system.fleet_monitoring.provider_ids import (
    FleetAgentProvider,
    provider_from_classified_name,
)


def test_membership_matches_known_providers() -> None:
    assert set(FleetAgentProvider) == {
        FleetAgentProvider.CLAUDE_CODE,
        FleetAgentProvider.CODEX,
        FleetAgentProvider.CURSOR,
        FleetAgentProvider.AIDER,
        FleetAgentProvider.GEMINI_CLI,
        FleetAgentProvider.ANTIGRAVITY_CLI,
        FleetAgentProvider.OPENCODE,
        FleetAgentProvider.KIMI,
        FleetAgentProvider.COPILOT,
    }


def test_members_round_trip_from_string() -> None:
    assert FleetAgentProvider("claude-code") is FleetAgentProvider.CLAUDE_CODE
    assert FleetAgentProvider("codex") is FleetAgentProvider.CODEX
    assert FleetAgentProvider.CLAUDE_CODE == "claude-code"


def test_cursor_family_resolves_to_provider_enum_members() -> None:
    assert provider_from_classified_name("cursor-claude-code") is FleetAgentProvider.CLAUDE_CODE
    assert provider_from_classified_name("cursor-agent-exec") is FleetAgentProvider.CURSOR
    assert provider_from_classified_name("codex-9999") is FleetAgentProvider.CODEX
    assert provider_from_classified_name("manual-bot") is None


def test_provider_values_are_stable() -> None:
    """Guards against accidental renaming of wire-format provider
    strings, which are matched against real process names."""
    assert {p.value for p in FleetAgentProvider} == {
        "claude-code",
        "codex",
        "cursor",
        "aider",
        "gemini-cli",
        "antigravity-cli",
        "opencode",
        "kimi",
        "copilot",
    }
