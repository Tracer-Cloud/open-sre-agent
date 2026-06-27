"""Per-model token pricing for the dashboard's ``$/hr`` column.

``$/hr`` is a *projected hourly burn rate* derived from the trailing
60 s usage window, not the actual spend over the last hour. The
sampler now keeps input/output/cache buckets, so pricing applies the
right rate to each bucket instead of using the legacy 70/30 blend.

The local price table is a vendored snapshot of the ``models.dev``
catalog for the Claude Code / Codex models this dashboard supports,
following CodexBar's offline-first approach. Unknown models return
``None`` so the dashboard renders ``-`` rather than inventing a rate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from tools.fleet_monitoring.meters import TokenUsage

#: ``models.dev`` pricing snapshot last refreshed for this vendored table.
RATES_VERIFIED_AT = "2026-05-17"

_USD_PER_M = 1_000_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    usd_per_input_token: float
    usd_per_output_token: float
    usd_per_cached_input_token: float | None = None
    usd_per_cache_read_input_token: float | None = None
    usd_per_cache_creation_input_token: float | None = None

    @property
    def cached_input_rate(self) -> float:
        return (
            self.usd_per_cached_input_token
            if self.usd_per_cached_input_token is not None
            else self.cache_read_rate
        )

    @property
    def cache_read_rate(self) -> float:
        return (
            self.usd_per_cache_read_input_token
            if self.usd_per_cache_read_input_token is not None
            else self.usd_per_input_token
        )

    @property
    def cache_creation_rate(self) -> float:
        return (
            self.usd_per_cache_creation_input_token
            if self.usd_per_cache_creation_input_token is not None
            else self.usd_per_input_token
        )


@dataclass(frozen=True)
class PriceOverride:
    """Per-agent rate override loaded from ``agents.yaml``.

    Overrides are USD per 1M input/output tokens. Cache rates keep the
    base model's ratios when the model is known; for custom unknown
    models they fall back to the effective input rate.
    """

    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None


def _price(
    input_usd_per_million: float,
    output_usd_per_million: float,
    *,
    cache_read_usd_per_million: float | None = None,
    cache_write_usd_per_million: float | None = None,
) -> ModelPrice:
    input_rate = input_usd_per_million / _USD_PER_M
    cache_read_rate = (
        cache_read_usd_per_million / _USD_PER_M if cache_read_usd_per_million is not None else None
    )
    return ModelPrice(
        usd_per_input_token=input_rate,
        usd_per_output_token=output_usd_per_million / _USD_PER_M,
        usd_per_cached_input_token=cache_read_rate,
        usd_per_cache_read_input_token=cache_read_rate,
        usd_per_cache_creation_input_token=(
            cache_write_usd_per_million / _USD_PER_M
            if cache_write_usd_per_million is not None
            else None
        ),
    )


MODEL_PRICES: dict[str, ModelPrice] = {
    # Anthropic / Claude Code. Values are USD per 1M tokens in
    # models.dev; _price converts them to USD per token.
    "claude-3-5-sonnet-20240620": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-3-5-sonnet-20241022": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-3-5-haiku-20241022": _price(
        0.80, 4.00, cache_read_usd_per_million=0.08, cache_write_usd_per_million=1.00
    ),
    "claude-3-5-haiku-latest": _price(
        0.80, 4.00, cache_read_usd_per_million=0.08, cache_write_usd_per_million=1.00
    ),
    "claude-haiku-4-5": _price(
        1.00, 5.00, cache_read_usd_per_million=0.10, cache_write_usd_per_million=1.25
    ),
    "claude-haiku-4-5-20251001": _price(
        1.00, 5.00, cache_read_usd_per_million=0.10, cache_write_usd_per_million=1.25
    ),
    "claude-sonnet-4": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-sonnet-4-0": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-sonnet-4-20250514": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-sonnet-4-5": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-sonnet-4-5-20250929": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-sonnet-4-6": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    "claude-opus-4": _price(
        15.00, 75.00, cache_read_usd_per_million=1.50, cache_write_usd_per_million=18.75
    ),
    "claude-opus-4-0": _price(
        15.00, 75.00, cache_read_usd_per_million=1.50, cache_write_usd_per_million=18.75
    ),
    "claude-opus-4-20250514": _price(
        15.00, 75.00, cache_read_usd_per_million=1.50, cache_write_usd_per_million=18.75
    ),
    "claude-opus-4-1": _price(
        15.00, 75.00, cache_read_usd_per_million=1.50, cache_write_usd_per_million=18.75
    ),
    "claude-opus-4-1-20250805": _price(
        15.00, 75.00, cache_read_usd_per_million=1.50, cache_write_usd_per_million=18.75
    ),
    "claude-opus-4-5": _price(
        5.00, 25.00, cache_read_usd_per_million=0.50, cache_write_usd_per_million=6.25
    ),
    "claude-opus-4-5-20251101": _price(
        5.00, 25.00, cache_read_usd_per_million=0.50, cache_write_usd_per_million=6.25
    ),
    "claude-opus-4-6": _price(
        5.00, 25.00, cache_read_usd_per_million=0.50, cache_write_usd_per_million=6.25
    ),
    "claude-opus-4-7": _price(
        5.00, 25.00, cache_read_usd_per_million=0.50, cache_write_usd_per_million=6.25
    ),
    # OpenAI / Codex.
    "gpt-4o": _price(2.50, 10.00, cache_read_usd_per_million=1.25),
    "gpt-4o-2024-05-13": _price(5.00, 15.00),
    "gpt-4o-2024-08-06": _price(2.50, 10.00, cache_read_usd_per_million=1.25),
    "gpt-4o-2024-11-20": _price(2.50, 10.00, cache_read_usd_per_million=1.25),
    "gpt-4o-mini": _price(0.15, 0.60, cache_read_usd_per_million=0.08),
    "gpt-5": _price(1.25, 10.00, cache_read_usd_per_million=0.125),
    "gpt-5-chat-latest": _price(1.25, 10.00),
    "gpt-5-codex": _price(1.25, 10.00, cache_read_usd_per_million=0.125),
    "gpt-5-mini": _price(0.25, 2.00, cache_read_usd_per_million=0.025),
    "gpt-5-nano": _price(0.05, 0.40, cache_read_usd_per_million=0.005),
    "gpt-5-pro": _price(15.00, 120.00),
    "gpt-5.1": _price(1.25, 10.00, cache_read_usd_per_million=0.13),
    "gpt-5.1-chat-latest": _price(1.25, 10.00, cache_read_usd_per_million=0.125),
    "gpt-5.1-codex": _price(1.25, 10.00, cache_read_usd_per_million=0.125),
    "gpt-5.1-codex-max": _price(1.25, 10.00, cache_read_usd_per_million=0.125),
    "gpt-5.1-codex-mini": _price(0.25, 2.00, cache_read_usd_per_million=0.025),
    "gpt-5.2": _price(1.75, 14.00, cache_read_usd_per_million=0.175),
    "gpt-5.2-chat-latest": _price(1.75, 14.00, cache_read_usd_per_million=0.175),
    "gpt-5.2-codex": _price(1.75, 14.00, cache_read_usd_per_million=0.175),
    "gpt-5.2-pro": _price(21.00, 168.00),
    "gpt-5.3-chat-latest": _price(1.75, 14.00, cache_read_usd_per_million=0.175),
    "gpt-5.3-codex": _price(1.75, 14.00, cache_read_usd_per_million=0.175),
    "gpt-5.3-codex-spark": _price(1.75, 14.00, cache_read_usd_per_million=0.175),
    "gpt-5.4": _price(2.50, 15.00, cache_read_usd_per_million=0.25),
    "gpt-5.4-mini": _price(0.75, 4.50, cache_read_usd_per_million=0.075),
    "gpt-5.4-nano": _price(0.20, 1.25, cache_read_usd_per_million=0.02),
    "gpt-5.4-pro": _price(30.00, 180.00),
    "gpt-5.5": _price(5.00, 30.00, cache_read_usd_per_million=0.50),
    "gpt-5.5-pro": _price(30.00, 180.00),
    "o3": _price(2.00, 8.00, cache_read_usd_per_million=0.50),
    "o3-deep-research": _price(10.00, 40.00, cache_read_usd_per_million=2.50),
    "o3-mini": _price(1.10, 4.40, cache_read_usd_per_million=0.55),
    "o3-pro": _price(20.00, 80.00),
}

_UNSORTED_FAMILY_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("claude-3-5-sonnet", "claude-3-5-sonnet-20241022"),
    ("claude-3-5-haiku", "claude-3-5-haiku-20241022"),
    ("claude-haiku-4-5", "claude-haiku-4-5"),
    ("claude-sonnet-4-6", "claude-sonnet-4-6"),
    ("claude-sonnet-4-5", "claude-sonnet-4-5"),
    ("claude-sonnet-4", "claude-sonnet-4"),
    ("claude-opus-4-7", "claude-opus-4-7"),
    ("claude-opus-4-6", "claude-opus-4-6"),
    ("claude-opus-4-5", "claude-opus-4-5"),
    ("claude-opus-4-1", "claude-opus-4-1"),
    ("claude-opus-4", "claude-opus-4"),
    ("gpt-5.3-codex-spark", "gpt-5.3-codex-spark"),
    ("gpt-5.3-codex", "gpt-5.3-codex"),
    ("gpt-5.2-codex", "gpt-5.2-codex"),
    ("gpt-5.1-codex-mini", "gpt-5.1-codex-mini"),
    ("gpt-5.1-codex-max", "gpt-5.1-codex-max"),
    ("gpt-5.1-codex", "gpt-5.1-codex"),
    ("gpt-5-codex", "gpt-5-codex"),
    ("gpt-5.5-pro", "gpt-5.5-pro"),
    ("gpt-5.5", "gpt-5.5"),
    ("gpt-5.4-mini", "gpt-5.4-mini"),
    ("gpt-5.4-nano", "gpt-5.4-nano"),
    ("gpt-5.4-pro", "gpt-5.4-pro"),
    ("gpt-5.4", "gpt-5.4"),
    ("gpt-5.2-pro", "gpt-5.2-pro"),
    ("gpt-5.2", "gpt-5.2"),
    ("gpt-5.1", "gpt-5.1"),
    ("gpt-5-mini", "gpt-5-mini"),
    ("gpt-5-nano", "gpt-5-nano"),
    ("gpt-5-pro", "gpt-5-pro"),
    ("gpt-5", "gpt-5"),
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o", "gpt-4o"),
    ("o3-deep-research", "o3-deep-research"),
    ("o3-mini", "o3-mini"),
    ("o3-pro", "o3-pro"),
    ("o3", "o3"),
)

# Longest-prefix-first so more specific families win. Build it
# programmatically so a future edit cannot silently shadow a longer
# family with its shorter prefix.
_FAMILY_FALLBACKS: tuple[tuple[str, str], ...] = tuple(
    sorted(_UNSORTED_FAMILY_FALLBACKS, key=lambda item: len(item[0]), reverse=True)
)


def usd_for_usage(
    usage: TokenUsage,
    model: str | None,
    override: PriceOverride | None = None,
) -> float | None:
    """Return USD for a structured usage sample.

    Codex reports ``cached_input_tokens`` as a discounted subset of
    ``input_tokens``. If a future format reports cached input as a
    disjoint counter, clamp to the current convention and log at
    debug level instead of producing a negative non-cached input
    total.
    """
    price = _resolve_price(model, override)
    if price is None:
        return None

    input_tokens = max(0.0, usage.input_tokens)
    raw_cached_input_tokens = max(0.0, usage.cached_input_tokens)
    if raw_cached_input_tokens > input_tokens:
        logger.debug(
            "cached_input_tokens exceeded input_tokens; clamping to input total",
            extra={
                "model": model,
                "input_tokens": input_tokens,
                "cached_input_tokens": raw_cached_input_tokens,
            },
        )
    cached_input_tokens = min(raw_cached_input_tokens, input_tokens)
    non_cached_input_tokens = input_tokens - cached_input_tokens
    return (
        non_cached_input_tokens * price.usd_per_input_token
        + cached_input_tokens * price.cached_input_rate
        + max(0.0, usage.output_tokens) * price.usd_per_output_token
        + max(0.0, usage.cache_read_input_tokens) * price.cache_read_rate
        + max(0.0, usage.cache_creation_input_tokens) * price.cache_creation_rate
    )


def usd_per_hour_for_usage(
    usage_per_min: TokenUsage,
    model: str | None,
    override: PriceOverride | None = None,
) -> float | None:
    cost_per_min = usd_for_usage(usage_per_min, model, override)
    if cost_per_min is None:
        return None
    return cost_per_min * 60.0


def usd_per_token_blended(model: str | None, override: PriceOverride | None = None) -> float | None:
    price = _resolve_price(model, override)
    if price is None:
        return None
    return 0.7 * price.usd_per_input_token + 0.3 * price.usd_per_output_token


def usd_per_hour(
    tokens_per_min: float,
    model: str | None,
    override: PriceOverride | None = None,
) -> float | None:
    """Legacy blended API kept for callers/tests that only have a total."""
    rate = usd_per_token_blended(model, override)
    if rate is None:
        return None
    return tokens_per_min * 60.0 * rate


def normalize_model_name(model: str | None) -> str | None:
    if model is None:
        return None
    candidates = _model_candidates(model)
    for candidate in candidates:
        if candidate in MODEL_PRICES:
            return candidate
    for candidate in candidates:
        for prefix, canonical_id in _FAMILY_FALLBACKS:
            if candidate.startswith(prefix):
                return canonical_id
    return candidates[0] if candidates else None


def _resolve_price(model: str | None, override: PriceOverride | None) -> ModelPrice | None:
    base = _lookup_price(model) if model is not None else None
    if override is None:
        return base

    input_rate = (
        override.input_usd_per_million / _USD_PER_M
        if override.input_usd_per_million is not None
        else (base.usd_per_input_token if base is not None else None)
    )
    output_rate = (
        override.output_usd_per_million / _USD_PER_M
        if override.output_usd_per_million is not None
        else (base.usd_per_output_token if base is not None else None)
    )
    if input_rate is None or output_rate is None:
        return None

    return ModelPrice(
        usd_per_input_token=input_rate,
        usd_per_output_token=output_rate,
        usd_per_cached_input_token=_override_related_rate(
            input_rate,
            base.usd_per_cached_input_token if base is not None else None,
            base.usd_per_input_token if base is not None else None,
        ),
        usd_per_cache_read_input_token=_override_related_rate(
            input_rate,
            base.usd_per_cache_read_input_token if base is not None else None,
            base.usd_per_input_token if base is not None else None,
        ),
        usd_per_cache_creation_input_token=_override_related_rate(
            input_rate,
            base.usd_per_cache_creation_input_token if base is not None else None,
            base.usd_per_input_token if base is not None else None,
        ),
    )


def _override_related_rate(
    effective_input_rate: float,
    base_related_rate: float | None,
    base_input_rate: float | None,
) -> float | None:
    if base_related_rate is None or base_input_rate is None or base_input_rate == 0.0:
        return None
    return effective_input_rate * (base_related_rate / base_input_rate)


def _lookup_price(model: str) -> ModelPrice | None:
    candidates = _model_candidates(model)
    for candidate in candidates:
        direct = MODEL_PRICES.get(candidate)
        if direct is not None:
            return direct
    for candidate in candidates:
        for prefix, canonical_id in _FAMILY_FALLBACKS:
            if candidate.startswith(prefix):
                resolved = MODEL_PRICES.get(canonical_id)
                if resolved is not None:
                    return resolved
    return None


@lru_cache(maxsize=512)
def _model_candidates(raw: str) -> tuple[str, ...]:
    candidates: list[str] = []

    def append(value: str) -> None:
        normalized = value.strip().lower()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    trimmed = raw.strip()
    append(trimmed)
    lower = trimmed.lower()
    for prefix in ("openai/", "anthropic/", "anthropic."):
        if lower.startswith(prefix):
            append(trimmed[len(prefix) :])

    if "claude-" in lower and "." in trimmed:
        tail = trimmed.rsplit(".", maxsplit=1)[-1]
        if tail.lower().startswith("claude-"):
            append(tail)

    index = 0
    while index < len(candidates):
        candidate = candidates[index]
        if "@" in candidate:
            base, suffix = candidate.split("@", maxsplit=1)
            append(base)
            if re.fullmatch(r"\d{8}", suffix):
                append(f"{base}-{suffix}")
        elif candidate.startswith("claude-"):
            append(f"{candidate}@default")

        for pattern in (r"-\d{4}-\d{2}-\d{2}$", r"-\d{8}$", r"-v\d+:\d+$"):
            stripped = re.sub(pattern, "", candidate)
            if stripped != candidate:
                append(stripped)
        index += 1

    return tuple(candidates)


__all__ = [
    "MODEL_PRICES",
    "ModelPrice",
    "PriceOverride",
    "RATES_VERIFIED_AT",
    "normalize_model_name",
    "usd_for_usage",
    "usd_per_hour",
    "usd_per_hour_for_usage",
    "usd_per_token_blended",
]
