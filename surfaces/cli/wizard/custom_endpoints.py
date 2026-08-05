"""Onboarding endpoint (base-URL) collection for the custom gateway providers.

Mirrors :mod:`surfaces.cli.wizard.azure_openai`: the provider-agnostic
:func:`surfaces.cli.wizard.endpoint_prompt.ensure_endpoint_settings` dispatcher
delegates here for ``custom-openai`` / ``custom-anthropic`` so all custom-gateway
prompting logic lives in one owning module instead of the shared dispatcher.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from surfaces.cli.wizard.config import ProviderOption


def _base_url_normalizer(provider: ProviderOption) -> Callable[[str], str]:
    """Return the base-URL normalizer for a custom provider.

    custom-anthropic uses the Anthropic-SDK normalizer (strips a trailing /v1,
    since the SDK appends /v1/messages); custom-openai keeps its /v1 verbatim.
    """
    from config.constants.llm import (
        normalize_anthropic_base_url,
        normalize_custom_base_url,
    )
    from core.llm.providers.custom_endpoints import is_custom_anthropic_provider

    if is_custom_anthropic_provider(provider.value):
        return normalize_anthropic_base_url
    return normalize_custom_base_url


def ensure_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Return the custom gateway base URL, prompting when it isn't set yet.

    Returns an empty dict when the provider declares no endpoint env, the
    endpoint mapping when configured/collected, or ``None`` when the user backs
    out of the prompt.
    """
    if not provider.endpoint_env:
        return {}
    normalize = _base_url_normalizer(provider)
    configured = normalize(os.getenv(provider.endpoint_env, ""))
    if configured:
        return {provider.endpoint_env: configured}
    return _prompt_endpoint(provider)


def _prompt_endpoint(provider: ProviderOption) -> dict[str, str] | None:
    from platform.terminal.theme import ERROR
    from surfaces.cli.wizard._ui import WizardBack, _console, _prompt_value, _step

    normalize = _base_url_normalizer(provider)
    _step("Endpoint")
    try:
        raw = _prompt_value(
            f"Base URL ({provider.endpoint_env})",
            default=os.getenv(provider.endpoint_env, provider.credential_default),
            secret=False,
            back_on_cancel=True,
        )
    except WizardBack:
        return None
    normalized = normalize(raw)
    if not normalized:
        _console.print(f"[{ERROR}]A base URL is required for this provider.[/]")
        return None
    return {provider.endpoint_env: normalized}
