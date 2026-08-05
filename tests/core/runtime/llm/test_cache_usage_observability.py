"""Prompt-cache hit rate must be observable from provider usage payloads.

Anthropic reports ``cache_read_input_tokens`` / ``cache_creation_input_tokens``;
OpenAI nests ``prompt_tokens_details.cached_tokens``. If reads stay zero across
identical-prefix requests, a silent invalidator is at work — these tests pin
that the counts survive extraction and land on the response and in the log.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from core.llm.shared.usage import (
    emit_provider_usage,
    extract_cache_tokens,
    llm_response_with_usage,
)


def _anthropic_usage(read: int, write: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


def test_anthropic_cache_fields_are_extracted() -> None:
    # Arrange / Act
    read, write = extract_cache_tokens(_anthropic_usage(9000, 1200))

    # Assert
    assert (read, write) == (9000, 1200)


def test_openai_nested_cached_tokens_are_extracted() -> None:
    # Arrange: OpenAI shape — cached tokens nested, no write-side count.
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 4096},
    }

    # Act
    read, write = extract_cache_tokens(usage)

    # Assert
    assert read == 4096
    assert write is None


def test_usage_without_cache_fields_is_none_not_zero() -> None:
    """Absent must stay distinguishable from a measured zero."""
    # Arrange / Act
    read, write = extract_cache_tokens(SimpleNamespace(input_tokens=5, output_tokens=1))

    # Assert
    assert read is None
    assert write is None


def test_response_carries_cache_tokens() -> None:
    # Arrange / Act
    response = llm_response_with_usage(
        "hello",
        "claude-opus-5",
        _anthropic_usage(9000, 1200),
        input_key="input_tokens",
        output_key="output_tokens",
    )

    # Assert
    assert response.cache_read_tokens == 9000
    assert response.cache_creation_tokens == 1200
    assert response.input_tokens == 100


def test_cache_usage_is_logged(caplog) -> None:
    """The greppable signal for verifying hit rate in gateway/REPL logs."""
    # Arrange
    caplog.set_level(logging.DEBUG, logger="core.llm.shared.usage")

    # Act
    emit_provider_usage(
        "claude-opus-5",
        _anthropic_usage(9000, 1200),
        input_key="input_tokens",
        output_key="output_tokens",
    )

    # Assert
    line = next(r.message for r in caplog.records if "llm-usage" in r.message)
    assert "cache_read=9000" in line
    assert "cache_write=1200" in line


def test_no_cache_fields_logs_without_cache_noise(caplog) -> None:
    # Arrange
    caplog.set_level(logging.DEBUG, logger="core.llm.shared.usage")

    # Act
    emit_provider_usage(
        "gpt-x",
        {"prompt_tokens": 10, "completion_tokens": 2},
        input_key="prompt_tokens",
        output_key="completion_tokens",
    )

    # Assert: the line exists but claims nothing about caching.
    line = next(r.message for r in caplog.records if "llm-usage" in r.message)
    assert "cache_read" not in line


def test_openai_responses_nested_cached_tokens_are_extracted() -> None:
    """The Responses API nests cache reads under ``input_tokens_details``."""
    # Arrange
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=30,
        input_tokens_details=SimpleNamespace(cached_tokens=384),
    )

    # Act
    read, write = extract_cache_tokens(usage)

    # Assert
    assert read == 384
    assert write is None


def test_openai_nested_cache_write_tokens_are_extracted() -> None:
    """Newer OpenAI models report a write-side count in the nested details."""
    # Arrange
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=30,
        input_tokens_details=SimpleNamespace(cached_tokens=384, cache_write_tokens=116),
    )

    # Act
    read, write = extract_cache_tokens(usage)

    # Assert
    assert read == 384
    assert write == 116
