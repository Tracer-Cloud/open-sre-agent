"""Tests for the token-meter registry (issues #1495, #2023)."""

from __future__ import annotations

import pytest

from tools.fleet_monitoring.meters import NullMeter, null_meter
from tools.fleet_monitoring.meters.claude_code import ClaudeCodeMeter
from tools.fleet_monitoring.meters.codex import CodexMeter
from tools.fleet_monitoring.meters.registry import TOKEN_METER_REGISTRY, get_token_meter


def test_claude_code_resolves_to_real_meter() -> None:
    """``claude-code`` has a real parser since #1567."""
    assert isinstance(get_token_meter("claude-code"), ClaudeCodeMeter)


def test_codex_resolves_to_real_meter() -> None:
    """``codex`` graduated from stub to real meter in #2023."""
    assert isinstance(get_token_meter("codex"), CodexMeter)


@pytest.mark.parametrize(
    "provider",
    ["cursor", "aider", "gemini-cli", "antigravity-cli", "opencode", "kimi", "copilot"],
)
def test_stub_providers_resolve_to_null_meter(provider: str) -> None:
    """Acceptance: stub providers exist in the registry and return 0.

    These are the providers waiting for a real parser in a follow-up
    issue; they must keep returning the null meter (which renders
    ``-`` in the dashboard) rather than raising on resolution.
    """
    meter = get_token_meter(provider)
    assert isinstance(meter, NullMeter)
    assert meter.parse_chunk('{"usage":{"input_tokens":999,"output_tokens":999}}') == 0


def test_unknown_provider_falls_back_to_null_meter() -> None:
    """A provider name not in the registry must not raise — fall back
    to the null meter so a new agent on the developer's machine can't
    crash the dashboard."""
    assert get_token_meter("brand-new-agent-xyz") is null_meter
    assert get_token_meter("").parse_chunk("anything") == 0


def test_registry_keys_cover_known_providers() -> None:
    """Drift guard: every name in :data:`KNOWN_PROVIDERS` must have an
    explicit registry entry. Otherwise a future provider added to
    ``providers.py`` but forgotten here would silently fall through
    to ``null_meter`` — correct fallback behavior, but masks the
    wiring bug.
    """
    from tools.fleet_monitoring.providers import KNOWN_PROVIDERS

    assert set(TOKEN_METER_REGISTRY) >= KNOWN_PROVIDERS


def test_registry_provider_names_are_lowercase_kebab() -> None:
    """Convention: provider identifiers in this codebase are
    lowercase-with-hyphen (matches ``integrations/llm_cli/registry.py``)."""
    for name in TOKEN_METER_REGISTRY:
        assert name == name.lower(), f"{name!r} must be lowercase"
        assert " " not in name, f"{name!r} must not contain spaces"
        assert "_" not in name, f"{name!r} must use hyphens, not underscores"
