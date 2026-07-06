"""Registry-wide contract: investigation tool schemas vs strict LLM adapters."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from core.llm.shared.tool_schema_normalize import (
    build_openai_tool_specs,
    normalize_openai_tool_input_schema,
)
from core.llm.transports.sdk.bedrock_converse import (
    build_converse_tool_specs,
    normalize_tool_input_schema,
)
from tests.core.runtime.llm.investigation_tool_schema_contract import (
    assert_all_investigation_tools_satisfy_strict_adapter,
)
from tools.registry import clear_tool_registry_cache


@pytest.fixture(autouse=True)
def _reset_tool_registry() -> Generator[None]:
    clear_tool_registry_cache()
    yield
    clear_tool_registry_cache()


def test_all_investigation_tool_schemas_satisfy_strict_adapter_invariants() -> None:
    """All investigation tools must pass the strictest shipped schema normalizer.

    Today that normalizer lives in ``bedrock_converse`` (strict JSON Schema tool specs).
    When a stricter provider adapter is added, point this test at its
    ``normalize_*`` / ``build_*_tool_specs`` helpers instead.
    """
    assert_all_investigation_tools_satisfy_strict_adapter(
        normalize_schema=normalize_tool_input_schema,
        build_tool_specs=build_converse_tool_specs,
    )


def test_all_investigation_tool_schemas_satisfy_openai_compat_adapter() -> None:
    """OpenAI-compatible providers (DeepSeek native API, etc.) require explicit ``type`` on every schema node."""
    assert_all_investigation_tools_satisfy_strict_adapter(
        normalize_schema=normalize_openai_tool_input_schema,
        build_tool_specs=build_openai_tool_specs,
    )


def test_all_investigation_tool_schemas_satisfy_litellm_compat_adapter() -> None:
    """LiteLLM receives the same OpenAI-compatible tool payload under the feature flag."""
    assert_all_investigation_tools_satisfy_strict_adapter(
        normalize_schema=normalize_openai_tool_input_schema,
        build_tool_specs=build_openai_tool_specs,
    )
