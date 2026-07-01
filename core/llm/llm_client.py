"""Dispatch entrypoint for non-agent LLM clients (reasoning, classification, toolcall)."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from integrations.llm_cli.registry import CLIProviderRegistration

from anthropic import BadRequestError as AnthropicBadRequestError
from anthropic import NotFoundError
from openai import APITimeoutError as OpenAITimeoutError
from openai import BadRequestError as OpenAIBadRequestError
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel, ValidationError

from config.config import (
    ANTHROPIC_LLM_CONFIG,
    OPENAI_LLM_CONFIG,
    resolve_llm_settings,
)
from config.llm_auth.auth_method import effective_llm_provider, get_configured_llm_auth_method
from core.domain.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES
from core.llm.openai_chat_completions import _RETRY_MAX_ATTEMPTS
from core.llm.openai_compat_providers import (
    ModelType,
    is_openai_compat_provider,
    resolve_openai_compat_provider,
)
from core.llm.provider_credentials import resolve_llm_api_key
from core.llm.transport_mode import current_llm_transport, use_litellm_transport
from core.llm.types import LLMResponse
from core.llm.usage import UsageHook, emit_usage, set_usage_hook

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


_LLMClientType = Any


class _LLMSingletonState:
    """Mutable holder for cached non-agent LLM clients and transport mode.

    Wrapped in a class so transport/client fields are read/written via attribute
    access on a stable container, avoiding the ``global`` keyword (which CodeQL's
    ``py/unused-global-variable`` rule misreports despite the in-function reads).
    """

    llm: _LLMClientType | None = None
    llm_for_classification: _LLMClientType | None = None
    llm_for_tools: _LLMClientType | None = None
    transport: str | None = None


_llm_state = _LLMSingletonState()


def reset_llm_singletons() -> None:
    """Clear cached LLM clients (tests, benchmarks, alternate configs)."""
    _llm_state.llm = None
    _llm_state.llm_for_classification = None
    _llm_state.llm_for_tools = None
    _llm_state.transport = None


def _ensure_llm_transport_current() -> None:
    transport = current_llm_transport()
    if _llm_state.transport != transport:
        _llm_state.llm = None
        _llm_state.llm_for_classification = None
        _llm_state.llm_for_tools = None
        _llm_state.transport = transport


def _get_cli_provider_registration(provider: str) -> CLIProviderRegistration | None:
    """Local import avoids package import cycle (llm_cli __init__ → runner → llm_client)."""
    from integrations.llm_cli.registry import get_cli_provider_registration

    return get_cli_provider_registration(provider)


def _select_model(settings: Any, provider_prefix: str, model_type: ModelType) -> str:
    attr = f"{provider_prefix}_{model_type}_model"
    return str(getattr(settings, attr))


def _create_llm_client(model_type: ModelType) -> _LLMClientType:
    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        errors = exc.errors()
        if len(errors) == 1:
            msg = re.sub(r"^[Vv]alue error,\s*", "", errors[0].get("msg", "")).strip()
            raise RuntimeError(msg or str(exc)) from exc
        raise RuntimeError(str(exc)) from exc

    provider = settings.provider
    runtime_provider = effective_llm_provider(provider, get_configured_llm_auth_method(provider))

    def _fallback_model(provider_prefix: str) -> str | None:
        if model_type == "toolcall":
            return None
        return _select_model(settings, provider_prefix, "toolcall")

    if (cli_reg := _get_cli_provider_registration(runtime_provider)) is not None:
        from config.config import DEFAULT_MAX_TOKENS
        from integrations.llm_cli.runner import CLIBackedLLMClient

        model_name = os.getenv(cli_reg.model_env_key, "").strip() or None
        return CLIBackedLLMClient(
            cli_reg.adapter_factory(),
            model=model_name,
            max_tokens=DEFAULT_MAX_TOKENS,
            model_type=model_type,
        )

    if use_litellm_transport():
        from core.llm.litellm.routing import build_litellm_llm_client

        return build_litellm_llm_client(
            settings,
            runtime_provider,
            model_type,
            usage_callback=emit_usage,
        )

    if runtime_provider == "openai":
        config = OPENAI_LLM_CONFIG
        sdk = _sdk_llm_clients_module()
        return sdk.OpenAILLMClient(
            model=_select_model(settings, "openai", model_type),
            model_fallback=_fallback_model("openai"),
            max_tokens=config.max_tokens,
        )
    elif is_openai_compat_provider(runtime_provider):
        compat = resolve_openai_compat_provider(settings, runtime_provider, model_type)
        fallback = _fallback_model(runtime_provider)
        sdk = _sdk_llm_clients_module()
        return sdk.OpenAILLMClient(
            model=compat.model,
            model_fallback=fallback,
            max_tokens=compat.config.max_tokens,
            base_url=compat.base_url,
            api_key_env=compat.api_key_env,
            api_key_default=compat.api_key_default,
            temperature=compat.temperature,
        )
    elif runtime_provider == "bedrock":
        from config.config import BEDROCK_LLM_CONFIG

        sdk = _sdk_llm_clients_module()
        return sdk.BedrockLLMClient(
            model=_select_model(settings, "bedrock", model_type),
            max_tokens=BEDROCK_LLM_CONFIG.max_tokens,
        )
    else:
        config = ANTHROPIC_LLM_CONFIG
        sdk = _sdk_llm_clients_module()
        return sdk.LLMClient(
            model=_select_model(settings, "anthropic", model_type),
            max_tokens=config.max_tokens,
        )


def get_llm_for_reasoning() -> _LLMClientType:
    """Return the singleton LLM client for complex reasoning tasks."""
    _ensure_llm_transport_current()
    if _llm_state.llm is None:
        _llm_state.llm = _create_llm_client(model_type="reasoning")
    return _llm_state.llm


def get_llm_for_classification() -> _LLMClientType:
    """Return the singleton LLM client for the mid-tier classification tier."""
    _ensure_llm_transport_current()
    if _llm_state.llm_for_classification is None:
        _llm_state.llm_for_classification = _create_llm_client(model_type="classification")
    return _llm_state.llm_for_classification


def get_llm_for_tools() -> _LLMClientType:
    """Return the singleton lightweight LLM client for tool selection / action planning."""
    _ensure_llm_transport_current()
    if _llm_state.llm_for_tools is None:
        _llm_state.llm_for_tools = _create_llm_client(model_type="toolcall")
    return _llm_state.llm_for_tools


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
