"""Unit tests for core.tool_framework.registry_metadata."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.tool_framework.registry_metadata import (
    BaseToolRegistryMetadata,
    normalize_surfaces,
    normalize_tags,
)


def test_registry_metadata_defaults() -> None:
    meta = BaseToolRegistryMetadata.model_validate({})
    assert meta.surfaces == ("investigation",)
    assert meta.tags == ()
    assert meta.parallel_safe is True


def test_registry_metadata_normalizes_surfaces() -> None:
    meta = BaseToolRegistryMetadata.model_validate({"surfaces": ("chat", "investigation")})
    assert meta.surfaces == ("chat", "investigation")


def test_registry_metadata_rejects_invalid_surface() -> None:
    with pytest.raises(ValidationError):
        BaseToolRegistryMetadata.model_validate({"surfaces": ("bogus",)})


def test_registry_metadata_normalizes_tags() -> None:
    meta = BaseToolRegistryMetadata.model_validate({"tags": (" fast ", "safe", "fast")})
    assert meta.tags == ("fast", "safe")


def test_normalize_surfaces_none_returns_default() -> None:
    assert normalize_surfaces(None) == ("investigation",)


def test_normalize_tags_deduplicates_and_strips() -> None:
    assert normalize_tags([" logs ", "metrics", "logs"]) == ("logs", "metrics")
