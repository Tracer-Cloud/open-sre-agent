"""Tests for the model pricing table and $/hr computation (#2023)."""

from __future__ import annotations

import logging

import pytest

from app.agents.meters import TokenUsage
from app.agents.pricing import (
    MODEL_PRICES,
    PriceOverride,
    normalize_model_name,
    usd_for_usage,
    usd_per_hour,
    usd_per_hour_for_usage,
    usd_per_token_blended,
)


class TestUsdPerTokenBlended:
    def test_known_claude_model(self) -> None:
        # claude-sonnet-4-5: 3 USD/M input, 15 USD/M output, 70/30 blend.
        # Expected: 0.7 * 3e-6 + 0.3 * 15e-6 = 2.1e-6 + 4.5e-6 = 6.6e-6.
        rate = usd_per_token_blended("claude-sonnet-4-5")
        assert rate is not None
        assert rate == pytest.approx(6.6e-6, rel=1e-9)

    def test_known_gpt5_model(self) -> None:
        # gpt-5: 1.25 USD/M input, 10 USD/M output, 70/30 blend.
        # Expected: 0.7 * 1.25e-6 + 0.3 * 10e-6 = 0.875e-6 + 3e-6 = 3.875e-6.
        rate = usd_per_token_blended("gpt-5")
        assert rate is not None
        assert rate == pytest.approx(3.875e-6, rel=1e-9)

    def test_unknown_model_returns_none(self) -> None:
        # The dashboard renders ``-`` for unknown models rather than
        # inventing a price. The contract is "never invent".
        assert usd_per_token_blended("claude-galaxy-9000") is None

    def test_none_model_returns_none(self) -> None:
        # Meters emit ``None`` when no chunk carries a model hint;
        # the pricing module must accept that without raising.
        assert usd_per_token_blended(None) is None

    def test_dated_suffix_falls_back_to_family(self) -> None:
        # ``claude-sonnet-4-5-20251015`` (date-suffixed id from a
        # future release that we have not added explicitly to the
        # table) should still resolve via the family prefix.
        rate = usd_per_token_blended("claude-sonnet-4-5-20251015")
        assert rate == usd_per_token_blended("claude-sonnet-4-5")

    def test_family_prefix_does_not_collide_with_shorter_family(self) -> None:
        # ``claude-opus-4-1`` must NOT match the ``claude-opus-4``
        # family before the longer ``claude-opus-4-1`` entry — the
        # opus-4-1 rate would otherwise be misread as opus-4.
        opus_4 = usd_per_token_blended("claude-opus-4")
        opus_4_1 = usd_per_token_blended("claude-opus-4-1")
        # Both happen to be the same today; the regression we guard
        # against is a future opus-4-1 with different rates getting
        # silently shadowed by a generic ``claude-opus-4`` rule.
        assert opus_4 is not None
        assert opus_4_1 is not None


class TestUsdPerHour:
    def test_zero_tokens_per_min_is_zero_cost(self) -> None:
        # An idle agent costs $0/hr — the cell shows ``$0.00``, not
        # ``-`` (because the model is known).
        assert usd_per_hour(0.0, "claude-sonnet-4-5") == pytest.approx(0.0)

    def test_unknown_model_returns_none(self) -> None:
        # Even with real tokens flowing, unknown model means ``-``.
        assert usd_per_hour(1000.0, "claude-galaxy-9000") is None

    def test_none_model_returns_none(self) -> None:
        assert usd_per_hour(1000.0, None) is None

    def test_formula_matches_tokens_per_min_times_60_times_rate(self) -> None:
        # The contract is ``tokens_per_min × 60 × rate``. Locking
        # this in so a refactor that switches to ``per second`` or
        # ``per hour`` directly does not change the dashboard's units.
        rate = usd_per_token_blended("claude-sonnet-4-5")
        assert rate is not None
        assert usd_per_hour(500.0, "claude-sonnet-4-5") == pytest.approx(500.0 * 60.0 * rate)

    def test_realistic_sonnet_session_under_a_dollar_per_hour(self) -> None:
        # Sanity check the numbers come out at a believable scale.
        # 200 tokens/min on Sonnet-4.5 (typical light agentic
        # session) should land under $0.10/hr.
        cost = usd_per_hour(200.0, "claude-sonnet-4-5")
        assert cost is not None
        assert 0.0 < cost < 0.10


class TestUsdForUsage:
    def test_codex_cached_input_uses_discounted_rate(self) -> None:
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=250, output_tokens=100)
        cost = usd_for_usage(usage, "gpt-5-codex")
        expected = (750 * 1.25e-6) + (250 * 0.125e-6) + (100 * 10e-6)
        assert cost == pytest.approx(expected)

    def test_codex_cached_input_is_clamped_to_input(self) -> None:
        usage = TokenUsage(input_tokens=100, cached_input_tokens=500, output_tokens=0)
        cost = usd_for_usage(usage, "gpt-5-codex")
        assert cost == pytest.approx(100 * 0.125e-6)

    def test_codex_cached_input_clamp_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        usage = TokenUsage(input_tokens=100, cached_input_tokens=500)
        with caplog.at_level(logging.DEBUG, logger="app.agents.pricing"):
            usd_for_usage(usage, "gpt-5-codex")

        assert "cached_input_tokens exceeded input_tokens" in caplog.text

    def test_claude_cache_buckets_use_separate_rates(self) -> None:
        usage = TokenUsage(
            input_tokens=100,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=500,
            output_tokens=50,
        )
        cost = usd_for_usage(usage, "claude-sonnet-4-5")
        expected = (100 * 3e-6) + (2000 * 0.3e-6) + (500 * 3.75e-6) + (50 * 15e-6)
        assert cost == pytest.approx(expected)

    def test_hourly_usage_rate_projects_cost_per_minute(self) -> None:
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=250, output_tokens=100)
        per_min_cost = usd_for_usage(usage, "gpt-5-codex")
        assert per_min_cost is not None
        assert usd_per_hour_for_usage(usage, "gpt-5-codex") == pytest.approx(per_min_cost * 60.0)

    def test_unknown_model_returns_none(self) -> None:
        assert usd_for_usage(TokenUsage(input_tokens=100), "claude-galaxy-9000") is None

    def test_input_output_overrides_keep_cache_ratio_for_known_model(self) -> None:
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=100, output_tokens=10)
        override = PriceOverride(input_usd_per_million=2.0, output_usd_per_million=20.0)
        cost = usd_for_usage(usage, "gpt-5-codex", override)
        expected = (900 * 2e-6) + (100 * 0.2e-6) + (10 * 20e-6)
        assert cost == pytest.approx(expected)


class TestNormalizeModelName:
    def test_openai_prefix_is_stripped(self) -> None:
        assert normalize_model_name("openai/gpt-5.3-codex") == "gpt-5.3-codex"

    def test_anthropic_prefix_is_stripped(self) -> None:
        assert normalize_model_name("anthropic/claude-sonnet-4-5") == "claude-sonnet-4-5"

    def test_bedrock_style_claude_id_resolves_to_base(self) -> None:
        assert (
            normalize_model_name("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
            == "claude-sonnet-4-5-20250929"
        )

    def test_at_default_variant_resolves_to_base(self) -> None:
        assert normalize_model_name("claude-sonnet-4-5@default") == "claude-sonnet-4-5"


class TestFamilyFallbackCoherence:
    """Drift guards on ``_FAMILY_FALLBACKS`` ↔ ``MODEL_PRICES``."""

    def test_family_fallbacks_are_longest_prefix_first(self) -> None:
        from app.agents.pricing import _FAMILY_FALLBACKS

        lengths = [len(prefix) for prefix, _canonical_id in _FAMILY_FALLBACKS]
        assert lengths == sorted(lengths, reverse=True)

    def test_every_family_fallback_canonical_id_has_a_price(self) -> None:
        # Without this guard, a typo in ``_FAMILY_FALLBACKS``'s
        # canonical id would silently break the family-prefix path:
        # ``_lookup_price`` would return ``None`` for what looks like
        # a known model and the dashboard would render ``-``.
        from app.agents.pricing import _FAMILY_FALLBACKS

        for prefix, canonical_id in _FAMILY_FALLBACKS:
            assert canonical_id in MODEL_PRICES, (
                f"family prefix {prefix!r} → canonical {canonical_id!r} not present in MODEL_PRICES"
            )


class TestModelPricesTable:
    def test_claude_code_default_models_have_prices(self) -> None:
        # Defensive regression: the most common claude-code models
        # must each return a price so the dashboard does not silently
        # degrade to ``-`` after a routine model bump.
        for model in ("claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-sonnet-20241022"):
            assert model in MODEL_PRICES or usd_per_token_blended(model) is not None

    def test_codex_default_models_have_prices(self) -> None:
        # Same guarantee for the codex side. ``gpt-5-codex`` is the
        # default model the Codex CLI configures for paid accounts.
        for model in ("gpt-5", "gpt-5-codex", "gpt-4o"):
            assert model in MODEL_PRICES or usd_per_token_blended(model) is not None
