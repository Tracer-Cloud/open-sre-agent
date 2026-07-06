"""Non-agent LLM client entrypoints (reasoning, classification, toolcall).

Provider routing lives in :mod:`core.llm.factory`; the ``get_llm_for_*`` names are
thin wrappers over it. Re-exports the streaming SDK client classes lazily (to avoid
a cycle with ``transports.sdk.llm_clients``). Root-cause parsing lives in
:mod:`core.llm.root_cause`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from anthropic import BadRequestError as AnthropicBadRequestError
from anthropic import NotFoundError
from openai import APITimeoutError as OpenAITimeoutError
from openai import BadRequestError as OpenAIBadRequestError
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel

from core.llm.factory import LLMRole, get_llm, reset_llm_clients
from core.llm.providers.provider_credentials import resolve_llm_api_key
from core.llm.shared.openai_chat_completions import _RETRY_MAX_ATTEMPTS
from core.llm.shared.usage import UsageHook, set_usage_hook
from core.llm.types import LLMResponse

# NOTE: The SDK client classes (``LLMClient``/``OpenAILLMClient``/``BedrockLLMClient``)
# are re-exported lazily via ``__getattr__`` below rather than statically imported from
# ``core.llm.transports.sdk.llm_clients``. ``llm_clients`` imports back into this module (for
# ``resolve_llm_api_key``), so a static import here — even under ``TYPE_CHECKING`` —
# would form a ``llm_client`` <-> ``sdk.llm_clients`` cycle (CodeQL ``py/cyclic-import``).

# ``LLMClient``/``OpenAILLMClient``/``BedrockLLMClient`` are intentionally omitted here:
# they are re-exported lazily through ``__getattr__`` (see ``_SDK_EXPORTS``) to avoid a
# static import of ``core.llm.transports.sdk.llm_clients``, which would reintroduce an import cycle.
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
    from core.llm.transports.sdk import llm_clients as module

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
