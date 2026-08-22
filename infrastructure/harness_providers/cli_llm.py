"""CLI-backed LLM adapters (``integrations.llm_cli``).

Core's LLM factory and transports build CLI-backed clients without importing the
``integrations.llm_cli`` package: it injects the registration lookup, the client
builder, and the message-flattening helper at boot.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from core.llm.types import CliLLMClient, ModelType

CliProviderRegistrationFn = Callable[[str], Any]


class BuildCliClientFn(Protocol):
    """Construct the CLI-backed client for a registered CLI adapter."""

    def __call__(
        self,
        adapter: Any,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        model_type: ModelType | None = None,
    ) -> CliLLMClient:
        """Build the CLI-backed client for *adapter*."""


FlattenCliMessagesFn = Callable[[list[dict[str, Any]]], str]


def _default_cli_provider_registration(_provider: str) -> Any:
    return None


def _cli_llm_backend_unavailable(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(
        "CLI LLM backend is not registered — call install_harness_providers() at startup."
    )


_cli_provider_registration_fn: CliProviderRegistrationFn = _default_cli_provider_registration
_build_cli_client_fn: BuildCliClientFn = _cli_llm_backend_unavailable
_flatten_cli_messages_fn: FlattenCliMessagesFn = _cli_llm_backend_unavailable


def cli_provider_registration(provider: str) -> Any:
    return _cli_provider_registration_fn(provider)


def build_cli_client(
    adapter: Any,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    model_type: ModelType | None = None,
) -> CliLLMClient:
    return _build_cli_client_fn(adapter, model=model, max_tokens=max_tokens, model_type=model_type)


def flatten_cli_messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    return _flatten_cli_messages_fn(messages)


def set_cli_llm_adapters(
    *,
    cli_provider_registration: CliProviderRegistrationFn | None = None,
    build_cli_client: BuildCliClientFn | None = None,
    flatten_cli_messages: FlattenCliMessagesFn | None = None,
) -> None:
    global _cli_provider_registration_fn, _build_cli_client_fn, _flatten_cli_messages_fn
    if cli_provider_registration is not None:
        _cli_provider_registration_fn = cli_provider_registration
    if build_cli_client is not None:
        _build_cli_client_fn = build_cli_client
    if flatten_cli_messages is not None:
        _flatten_cli_messages_fn = flatten_cli_messages


def reset() -> None:
    """Restore the unavailable-backend defaults (tests)."""
    set_cli_llm_adapters(
        cli_provider_registration=_default_cli_provider_registration,
        build_cli_client=_cli_llm_backend_unavailable,
        flatten_cli_messages=_cli_llm_backend_unavailable,
    )


__all__ = [
    "BuildCliClientFn",
    "CliProviderRegistrationFn",
    "FlattenCliMessagesFn",
    "build_cli_client",
    "cli_provider_registration",
    "flatten_cli_messages_to_prompt",
    "set_cli_llm_adapters",
]
