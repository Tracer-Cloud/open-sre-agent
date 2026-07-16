"""Structured failures raised at hosted LLM provider boundaries."""

from __future__ import annotations


class LLMResourceNotFoundError(RuntimeError):
    """A configured provider resource, such as a model or deployment, was not found."""

    def __init__(
        self,
        *,
        provider: str,
        provider_label: str | None = None,
        resource_kind: str,
        resource_name: str,
        detail: str | None = None,
    ) -> None:
        self.provider = provider
        self.provider_label = provider_label or provider
        self.resource_kind = resource_kind
        self.resource_name = resource_name
        default_detail = f"{self.provider_label} {resource_kind} '{resource_name}' was not found."
        super().__init__(detail or default_detail)


__all__ = ["LLMResourceNotFoundError"]
