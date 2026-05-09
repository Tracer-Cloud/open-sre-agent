"""Tests for the token-meter registry (issue #1495)."""

from __future__ import annotations

import pytest

from app.agents.meters import NullMeter, null_meter
from app.agents.meters.claude_code import ClaudeCodeMeter
from app.agents.meters.registry import TOKEN_METER_REGISTRY, get_token_meter


def test_claude_code_resolves_to_real_meter() -> None:
    """``claude-code`` is the only provider with a real parser in this PR."""
    assert isinstance(get_token_meter("claude-code"), ClaudeCodeMeter)


@pytest.mark.parametrize(
    "provider",
    ["codex", "cursor", "aider", "gemini-cli", "opencode", "kimi"],
)
def test_stub_providers_resolve_to_null_meter(provider: str) -> None:
    """Acceptance: stub providers exist in the registry and return 0."""
    meter = get_token_meter(provider)
    assert isinstance(meter, NullMeter)
    assert meter.parse_chunk('{"usage":{"input_tokens":999,"output_tokens":999}}') == 0


def test_unknown_provider_falls_back_to_null_meter() -> None:
    """A provider name not in the registry must not raise — fall back
    to the null meter so a new agent on the developer's machine can't
    crash the dashboard."""
    assert get_token_meter("brand-new-agent-xyz") is null_meter
    assert get_token_meter("").parse_chunk("anything") == 0


def test_registry_provider_names_are_lowercase_kebab() -> None:
    """Convention: provider identifiers in this codebase are
    lowercase-with-hyphen (matches ``app/integrations/llm_cli/registry.py``)."""
    for name in TOKEN_METER_REGISTRY:
        assert name == name.lower(), f"{name!r} must be lowercase"
        assert " " not in name, f"{name!r} must not contain spaces"
        assert "_" not in name, f"{name!r} must use hyphens, not underscores"
