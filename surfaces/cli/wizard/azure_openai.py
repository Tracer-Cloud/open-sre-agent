"""Azure OpenAI wizard helpers: endpoint setup, deployment picker, validation."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from core.llm.providers.azure_openai import (
    discover_azure_openai_deployments_from_env,
    format_azure_deployment_not_found_message,
    is_azure_deployment_lookup_error,
    is_azure_openai_provider,
    list_azure_openai_deployments,
    normalize_azure_openai_base_url,
    resolve_azure_openai_api_version,
)
from platform.terminal.theme import ERROR, WARNING
from surfaces.cli.wizard._ui import (
    _CUSTOM_MODEL_SENTINEL,
    Choice,
    WizardBack,
    _choose,
    _choose_model,
    _console,
    _prompt_value,
    _step,
)

if TYPE_CHECKING:
    from surfaces.cli.wizard.config import ProviderOption
    from surfaces.cli.wizard.validation import ValidationResult


def endpoint_env(provider: ProviderOption) -> dict[str, str]:
    """Return Azure endpoint env vars, using the default API version when unset."""
    return {
        provider.endpoint_env: os.getenv(provider.endpoint_env, "").strip(),
        provider.api_version_env: resolve_azure_openai_api_version(),
    }


def prompt_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Collect Azure OpenAI resource URL during onboarding."""
    if not provider.endpoint_env or not provider.api_version_env:
        return {}

    _step("Azure endpoint")
    try:
        base_url = _prompt_value(
            f"Azure OpenAI resource URL ({provider.endpoint_env})",
            default=os.getenv(provider.endpoint_env, provider.credential_default),
            secret=False,
            back_on_cancel=True,
        )
    except WizardBack:
        return None

    normalized_base = normalize_azure_openai_base_url(base_url)
    if not normalized_base:
        _console.print(f"[{ERROR}]Azure OpenAI resource URL is required.[/]")
        return None
    return {
        provider.endpoint_env: normalized_base,
        provider.api_version_env: resolve_azure_openai_api_version(),
    }


def ensure_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Return Azure endpoint env vars, prompting when missing."""
    from core.llm.providers.azure_openai import azure_openai_endpoint_configured

    if not is_azure_openai_provider(provider.value):
        return {}
    if azure_openai_endpoint_configured():
        return endpoint_env(provider)
    return prompt_endpoint_settings(provider)


def choose_azure_deployment(
    *,
    default: str | None,
    model_env: str = "AZURE_OPENAI_REASONING_MODEL",
    back_on_cancel: bool = False,
) -> str:
    """Prompt for an Azure OpenAI deployment name from the user's resource."""
    _step("Deployment")

    resolved_default = (default or "").strip()
    deployments = discover_azure_openai_deployments_from_env()
    if not deployments:
        _console.print(
            f"[{WARNING}]Could not list deployments from your Azure resource. "
            "Enter the deployment name from the Azure portal.[/]"
        )
        return _prompt_value(
            f"Azure OpenAI deployment name ({model_env})",
            default=resolved_default,
            allow_empty=False,
            back_on_cancel=back_on_cancel,
        )

    deployment_choices = [
        Choice(value=deployment, label=deployment, hint="deployment") for deployment in deployments
    ]
    extra_choices: list[Choice] = []
    if resolved_default and resolved_default not in deployments:
        extra_choices.append(Choice(value=resolved_default, label=resolved_default, hint="current"))

    custom_choice = Choice(
        value=_CUSTOM_MODEL_SENTINEL,
        label="Enter custom deployment name",
        hint="type deployment name from Azure portal",
    )
    choices = deployment_choices + extra_choices + [custom_choice]
    default_value = resolved_default or deployments[0]
    if default_value and not any(choice.value == default_value for choice in choices):
        default_value = deployments[0]

    selection = _choose(
        "Choose Azure OpenAI deployment",
        choices,
        default=default_value or None,
        back_on_cancel=back_on_cancel,
    )
    if selection != _CUSTOM_MODEL_SENTINEL:
        return selection

    return _prompt_value(
        f"Custom Azure OpenAI deployment name ({model_env})",
        default=resolved_default,
        allow_empty=False,
        back_on_cancel=back_on_cancel,
    )


def choose_provider_model(
    provider: ProviderOption,
    model_provider: ProviderOption,
    *,
    default: str | None,
    prompt_label: str | None = None,
    back_on_cancel: bool = False,
) -> str:
    """Prompt for a model or Azure deployment after provider credentials are set."""
    if is_azure_openai_provider(provider.value):
        return choose_azure_deployment(
            default=default,
            model_env=model_provider.model_env,
            back_on_cancel=back_on_cancel,
        )
    return _choose_model(
        model_provider,
        default=default,
        prompt_label=prompt_label,
        back_on_cancel=back_on_cancel,
    )


def format_validation_failure(
    *,
    deployment: str,
    base_url: str,
    api_key: str,
    api_version: str,
    error: Exception,
) -> str:
    """Explain Azure validation failures, listing deployments when possible."""
    if not is_azure_deployment_lookup_error(error):
        return f"Validation request failed: {error}"

    detail = format_azure_deployment_not_found_message(deployment)
    available = list_azure_openai_deployments(
        base_url=base_url,
        api_key=api_key,
        api_version=api_version,
    )
    if available:
        detail += f" Available deployments: {', '.join(available)}"
    return detail


def validate_credentials(
    *,
    api_key: str,
    deployment: str,
    base_url: str,
    api_version: str,
) -> ValidationResult:
    """Validate Azure OpenAI credentials with a tiny chat completion."""
    from surfaces.cli.wizard.validation import ValidationResult, _load_openai_client

    normalized_base = normalize_azure_openai_base_url(base_url)
    if not normalized_base:
        return ValidationResult(
            ok=False,
            detail="Azure OpenAI resource URL is missing. Set AZURE_OPENAI_BASE_URL.",
        )

    resolved_api_version = resolve_azure_openai_api_version(api_version)
    openai_client_cls, openai_auth_error = _load_openai_client()
    azure_base = f"{normalized_base}/openai/deployments/{deployment}"
    try:
        client = openai_client_cls(
            api_key=api_key,
            base_url=azure_base,
            default_query={"api-version": resolved_api_version},
            timeout=30.0,
        )
        request_kwargs: dict[str, object] = {
            "model": deployment,
            "messages": [{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
        }
        if deployment.startswith(("o1", "o3", "o4", "gpt-5")):
            request_kwargs["max_completion_tokens"] = 24
        else:
            request_kwargs["max_tokens"] = 24
        response = client.chat.completions.create(**request_kwargs)
        sample_text = (response.choices[0].message.content or "").strip()
        return ValidationResult(
            ok=True,
            detail="Azure OpenAI API key validated.",
            sample_response=sample_text,
        )
    except openai_auth_error:
        return ValidationResult(ok=False, detail="Azure OpenAI rejected the API key.")
    except Exception as err:
        return ValidationResult(
            ok=False,
            detail=format_validation_failure(
                deployment=deployment,
                base_url=base_url,
                api_key=api_key,
                api_version=api_version,
                error=err,
            ),
        )
