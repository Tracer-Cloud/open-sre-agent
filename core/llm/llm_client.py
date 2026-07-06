"""Non-agent LLM clients (reasoning, classification, toolcall) and RCA parsing.

Provider routing lives in :mod:`core.llm.factory`; the ``get_llm_for_*`` names are
thin wrappers over it. This module also owns root-cause parsing and re-exports the
streaming SDK client classes (lazily, to avoid a cycle with ``sdk.llm_clients``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import BadRequestError as AnthropicBadRequestError
from anthropic import NotFoundError
from openai import APITimeoutError as OpenAITimeoutError
from openai import BadRequestError as OpenAIBadRequestError
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel

from core.domain.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES
from core.llm.factory import LLMRole, get_llm, reset_llm_clients
from core.llm.openai_chat_completions import _RETRY_MAX_ATTEMPTS
from core.llm.provider_credentials import resolve_llm_api_key
from core.llm.types import LLMResponse
from core.llm.usage import UsageHook, set_usage_hook

# NOTE: The SDK client classes (``LLMClient``/``OpenAILLMClient``/``BedrockLLMClient``)
# are re-exported lazily via ``__getattr__`` below rather than statically imported from
# ``core.llm.sdk.llm_clients``. ``llm_clients`` imports back into this module (for
# ``resolve_llm_api_key``), so a static import here — even under ``TYPE_CHECKING`` —
# would form a ``llm_client`` <-> ``sdk.llm_clients`` cycle (CodeQL ``py/cyclic-import``).

# ``LLMClient``/``OpenAILLMClient``/``BedrockLLMClient`` are intentionally omitted here:
# they are re-exported lazily through ``__getattr__`` (see ``_SDK_EXPORTS``) to avoid a
# static import of ``core.llm.sdk.llm_clients``, which would reintroduce an import cycle.
__all__ = [
    "OpenAIRateLimitError",
    "OpenAIBadRequestError",
    "OpenAITimeoutError",
    "UsageHook",
    "set_usage_hook",
    "get_llm_for_reasoning",
    "get_llm_for_classification",
    "get_llm_for_tools",
    "reset_llm_singletons",
    "LLMResponse",
    "RootCauseResult",
    "parse_root_cause",
    "SupportsLLMInvoke",
    "resolve_llm_api_key",
]

_SDK_EXPORTS = frozenset(
    {
        "LLMClient",
        "OpenAILLMClient",
        "BedrockLLMClient",
        "_format_anthropic_retry_error",
        "_format_openai_connection_error",
        "_is_anthropic_bedrock_model",
    }
)

# Re-exported for tests (``tests/core/runtime/llm/test_llm_client.py``).
_ = (
    AnthropicBadRequestError,
    NotFoundError,
    _RETRY_MAX_ATTEMPTS,
)


def _sdk_llm_clients_module() -> Any:
    from core.llm.sdk import llm_clients as module

    return module


def __getattr__(name: str) -> Any:
    if name in _SDK_EXPORTS:
        return getattr(_sdk_llm_clients_module(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class SupportsLLMInvoke(Protocol):
    def with_config(self, **_kwargs: Any) -> SupportsLLMInvoke:
        pass

    def with_structured_output(self, model: type[BaseModel]) -> Any:
        pass

    def bind_tools(self, _tools: list[Any]) -> SupportsLLMInvoke:
        pass

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        pass

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        pass


@dataclass(frozen=True)
class RootCauseResult:
    root_cause: str
    root_cause_category: str
    validated_claims: list[str]
    non_validated_claims: list[str]
    causal_chain: list[str]
    remediation_steps: list[str]


def reset_llm_singletons() -> None:
    """Clear cached LLM clients (tests, benchmarks, alternate configs)."""
    reset_llm_clients()


def _create_llm_client(model_type: Any) -> Any:
    """Build a fresh (uncached) reasoning-family client — the routing lives in the factory."""
    from core.llm.factory import build_llm_client

    return build_llm_client(model_type)


def get_llm_for_reasoning() -> Any:
    """Return the singleton LLM client for complex reasoning tasks."""
    return get_llm(LLMRole.REASONING)


def get_llm_for_classification() -> Any:
    """Return the singleton LLM client for the mid-tier classification tier."""
    return get_llm(LLMRole.CLASSIFICATION)


def get_llm_for_tools() -> Any:
    """Return the singleton lightweight LLM client for tool selection / action planning."""
    return get_llm(LLMRole.TOOLCALL)


def parse_root_cause(response: str) -> RootCauseResult:
    """Parse root cause, category, and claims from LLM response."""
    root_cause = "Unable to determine root cause"
    root_cause_category = "unknown"
    validated_claims: list[str] = []
    non_validated_claims: list[str] = []
    causal_chain: list[str] = []
    remediation_steps: list[str] = []

    if "ROOT_CAUSE_CATEGORY:" in response:
        parts = response.split("ROOT_CAUSE_CATEGORY:", 1)
        if len(parts) > 1:
            after = parts[1]
            for line in after.split("\n"):
                candidate = line.strip().lower()
                if not candidate:
                    continue
                if candidate in VALID_ROOT_CAUSE_CATEGORIES:
                    root_cause_category = candidate
                    break
                for token in re.findall(r"[a-z_][a-z0-9_]*", candidate):
                    if token in VALID_ROOT_CAUSE_CATEGORIES:
                        root_cause_category = token
                        break
                if root_cause_category != "unknown":
                    break

    if "ROOT_CAUSE:" in response:
        parts = response.split("ROOT_CAUSE:", 1)
        if len(parts) > 1:
            after = parts[1]
            for delimiter in (
                "ROOT_CAUSE_CATEGORY:",
                "VALIDATED_CLAIMS:",
                "NON_VALIDATED_CLAIMS:",
                "CAUSAL_CHAIN:",
                "REMEDIATION_STEPS:",
            ):
                if delimiter in after:
                    root_cause = after.split(delimiter, 1)[0].strip()
                    break
            else:
                root_cause = after.strip()

            if "VALIDATED_CLAIMS:" in after:
                validated_section = after.split("VALIDATED_CLAIMS:", 1)[1]
                for delimiter in (
                    "NON_VALIDATED_CLAIMS:",
                    "CAUSAL_CHAIN:",
                    "REMEDIATION_STEPS:",
                ):
                    if delimiter in validated_section:
                        validated_text = validated_section.split(delimiter, 1)[0]
                        break
                else:
                    validated_text = validated_section

                for line in validated_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if (
                        line
                        and not line.startswith("NON_")
                        and not line.startswith("CAUSAL_CHAIN")
                        and not line.startswith("CONFIDENCE")
                        and not line.startswith("ROOT_CAUSE")
                        and not line.startswith("REMEDIATION_STEPS")
                    ):
                        validated_claims.append(line)

            if "NON_VALIDATED_CLAIMS:" in after:
                non_validated_section = after.split("NON_VALIDATED_CLAIMS:", 1)[1]
                for delimiter in (
                    "ALTERNATIVE_HYPOTHESES_CONSIDERED:",
                    "CAUSAL_CHAIN:",
                    "REMEDIATION_STEPS:",
                ):
                    if delimiter in non_validated_section:
                        non_validated_text = non_validated_section.split(delimiter, 1)[0]
                        break
                else:
                    non_validated_text = non_validated_section

                for line in non_validated_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if (
                        line
                        and not line.startswith("CAUSAL_CHAIN")
                        and not line.startswith("ALTERNATIVE")
                        and not line.startswith("REMEDIATION_STEPS")
                    ):
                        non_validated_claims.append(line)

            if "CAUSAL_CHAIN:" in after:
                causal_section = after.split("CAUSAL_CHAIN:", 1)[1]
                if "REMEDIATION_STEPS:" in causal_section:
                    causal_section = causal_section.split("REMEDIATION_STEPS:", 1)[0]
                causal_text = causal_section

                for line in causal_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if line and not line.startswith("ALTERNATIVE"):
                        causal_chain.append(line)

            if "REMEDIATION_STEPS:" in after:
                rem_section = after.split("REMEDIATION_STEPS:", 1)[1]
                for line in rem_section.strip().split("\n"):
                    line = line.strip().lstrip("*-•( ").strip()
                    if not line or line.startswith("("):
                        continue
                    if any(
                        line.startswith(h)
                        for h in (
                            "ROOT_CAUSE",
                            "VALIDATED",
                            "NON_VALIDATED",
                            "CAUSAL",
                            "ALTERNATIVE",
                            "REMEDIATION_STEPS",
                        )
                    ):
                        break
                    remediation_steps.append(line)

    return RootCauseResult(
        root_cause=root_cause,
        root_cause_category=root_cause_category,
        validated_claims=validated_claims,
        non_validated_claims=non_validated_claims,
        causal_chain=causal_chain,
        remediation_steps=remediation_steps,
    )
