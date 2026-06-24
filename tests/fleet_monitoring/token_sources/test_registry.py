"""Tests for the token-source registry (#2023)."""

from __future__ import annotations

import pytest

from app.fleet_monitoring.token_sources import NullTokenSource, null_token_source
from app.fleet_monitoring.token_sources.claude_code import ClaudeCodeJsonlSource
from app.fleet_monitoring.token_sources.codex import CodexRolloutSource
from app.fleet_monitoring.token_sources.registry import (
    TOKEN_SOURCE_REGISTRY,
    get_token_source,
)


def test_claude_code_resolves_to_real_source() -> None:
    assert isinstance(get_token_source("claude-code"), ClaudeCodeJsonlSource)


def test_codex_resolves_to_real_source() -> None:
    assert isinstance(get_token_source("codex"), CodexRolloutSource)


@pytest.mark.parametrize(
    "provider",
    ["cursor", "aider", "gemini-cli", "antigravity-cli", "opencode", "kimi", "copilot"],
)
def test_stub_providers_resolve_to_null_source(provider: str) -> None:
    source = get_token_source(provider)
    assert isinstance(source, NullTokenSource)
    # ``read_new_chunk`` on the null source must return ``None`` (not
    # ``""``) so the dashboard renders ``-`` instead of ``0``.
    assert source.read_new_chunk(9999) is None


def test_unknown_provider_falls_back_to_null_source() -> None:
    # The wiring layer asks for a source by provider name; an
    # unknown provider must not crash. Fall back to ``null_token_source``.
    assert get_token_source("brand-new-cli-xyz") is null_token_source


def test_registry_singletons_are_stable() -> None:
    # Sources hold per-PID state; constructing a fresh instance on
    # every lookup would defeat the incremental-read optimization.
    # Lock the singleton-identity invariant in.
    assert get_token_source("claude-code") is get_token_source("claude-code")
    assert get_token_source("codex") is get_token_source("codex")


def test_registry_provider_names_match_meter_registry() -> None:
    # The sampler resolves a provider once and uses the same string
    # against both registries; they must agree on every key.
    from app.fleet_monitoring.meters.registry import TOKEN_METER_REGISTRY

    assert set(TOKEN_SOURCE_REGISTRY) == set(TOKEN_METER_REGISTRY)


def test_registry_keys_cover_known_providers() -> None:
    # Drift guard: every provider that ``provider_for`` can resolve
    # must have an explicit registry entry (real or null). Without
    # this check, adding a name to ``providers.KNOWN_PROVIDERS``
    # while forgetting both registry tables would silently fall
    # through ``get_token_source`` to ``null_token_source`` — the
    # right behavior, but masks the wiring bug.
    from app.fleet_monitoring.providers import KNOWN_PROVIDERS

    assert set(TOKEN_SOURCE_REGISTRY) >= KNOWN_PROVIDERS
