"""Explicit provider-boundary hooks for the runtime agent loop."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.runtime.messages import ProviderMessage, RuntimeMessage


@dataclass(frozen=True)
class ProviderRequest:
    """Provider request payload before the concrete LLM client is invoked."""

    messages: list[ProviderMessage]
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


TransformContextHook = Callable[[Sequence["RuntimeMessage"]], Sequence["RuntimeMessage"]]
ConvertToLlmHook = Callable[[Any, Sequence["RuntimeMessage"]], list["ProviderMessage"]]
BeforeProviderRequestHook = Callable[[ProviderRequest], ProviderRequest | None]
AfterProviderResponseHook = Callable[[ProviderRequest, Any], Any | None]
ApiKeyResolver = Callable[[str], str]


@dataclass(frozen=True)
class ProviderHooks:
    """Hooks around context conversion, credentials, and provider requests."""

    transform_context: TransformContextHook | None = None
    convert_to_llm: ConvertToLlmHook | None = None
    before_provider_request: BeforeProviderRequestHook | None = None
    after_provider_response: AfterProviderResponseHook | None = None
    get_api_key: ApiKeyResolver | None = None

    def apply_transform_context(
        self,
        messages: Sequence[RuntimeMessage],
    ) -> list[RuntimeMessage]:
        if self.transform_context is None:
            return list(messages)
        return list(self.transform_context(messages))

    def apply_convert_to_llm(
        self,
        llm: Any,
        messages: Sequence[RuntimeMessage],
    ) -> list[ProviderMessage]:
        if self.convert_to_llm is None:
            from core.runtime.messages import convert_to_llm_messages

            return convert_to_llm_messages(llm, messages)
        return self.convert_to_llm(llm, messages)

    def apply_before_request(self, request: ProviderRequest) -> ProviderRequest:
        if self.before_provider_request is None:
            return request
        updated = self.before_provider_request(request)
        return request if updated is None else updated

    def apply_after_response(self, request: ProviderRequest, response: Any) -> Any:
        if self.after_provider_response is None:
            return response
        updated = self.after_provider_response(request, response)
        return response if updated is None else updated

    def with_metadata(self, request: ProviderRequest, **metadata: Any) -> ProviderRequest:
        return replace(request, metadata={**request.metadata, **metadata})


def resolve_llm_api_key(env_name: str) -> str:
    """Default runtime credential resolver."""
    from config.llm_credentials import resolve_llm_api_key as _resolve

    return _resolve(env_name)


__all__ = [
    "ApiKeyResolver",
    "ProviderHooks",
    "ProviderRequest",
    "resolve_llm_api_key",
]
