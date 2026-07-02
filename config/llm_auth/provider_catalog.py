"""Canonical LLM provider auth and model-selection metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CredentialKind = Literal["api_key", "cli", "ambient", "local"]


@dataclass(frozen=True)
class ProviderSpec:
    """Provider facts shared by config, auth, wizard, and runtime checks."""

    value: str
    label: str
    credential_kind: CredentialKind
    api_key_env: str = ""
    model_env: str = ""
    legacy_model_env: str | None = None
    toolcall_model_env: str | None = None
    classification_model_env: str | None = None
    cli_model_env: str | None = None
    endpoint_env: str = ""
    api_version_env: str = ""
    allow_custom_models: bool = False

    @property
    def uses_open_sre_api_key(self) -> bool:
        return self.credential_kind == "api_key" and bool(self.api_key_env)


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        value="anthropic",
        label="Anthropic API key",
        credential_kind="api_key",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_REASONING_MODEL",
        legacy_model_env="ANTHROPIC_MODEL",
        toolcall_model_env="ANTHROPIC_TOOLCALL_MODEL",
        classification_model_env="ANTHROPIC_CLASSIFICATION_MODEL",
    ),
    ProviderSpec(
        value="openai",
        label="OpenAI API key",
        credential_kind="api_key",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_REASONING_MODEL",
        legacy_model_env="OPENAI_MODEL",
        toolcall_model_env="OPENAI_TOOLCALL_MODEL",
        classification_model_env="OPENAI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="openrouter",
        label="OpenRouter",
        credential_kind="api_key",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_REASONING_MODEL",
        legacy_model_env="OPENROUTER_MODEL",
        toolcall_model_env="OPENROUTER_TOOLCALL_MODEL",
        classification_model_env="OPENROUTER_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="deepseek",
        label="DeepSeek",
        credential_kind="api_key",
        api_key_env="DEEPSEEK_API_KEY",
        model_env="DEEPSEEK_REASONING_MODEL",
        legacy_model_env="DEEPSEEK_MODEL",
        toolcall_model_env="DEEPSEEK_TOOLCALL_MODEL",
        classification_model_env="DEEPSEEK_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="gemini",
        label="Google Gemini API key",
        credential_kind="api_key",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_REASONING_MODEL",
        legacy_model_env="GEMINI_MODEL",
        toolcall_model_env="GEMINI_TOOLCALL_MODEL",
        classification_model_env="GEMINI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="nvidia",
        label="NVIDIA NIM",
        credential_kind="api_key",
        api_key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_REASONING_MODEL",
        legacy_model_env="NVIDIA_MODEL",
        toolcall_model_env="NVIDIA_TOOLCALL_MODEL",
        classification_model_env="NVIDIA_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="minimax",
        label="MiniMax",
        credential_kind="api_key",
        api_key_env="MINIMAX_API_KEY",
        model_env="MINIMAX_REASONING_MODEL",
        legacy_model_env="MINIMAX_MODEL",
        toolcall_model_env="MINIMAX_TOOLCALL_MODEL",
        classification_model_env="MINIMAX_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="groq",
        label="Groq API key",
        credential_kind="api_key",
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_REASONING_MODEL",
        legacy_model_env="GROQ_MODEL",
        toolcall_model_env="GROQ_TOOLCALL_MODEL",
        classification_model_env="GROQ_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="azure-openai",
        label="Azure OpenAI",
        credential_kind="api_key",
        api_key_env="AZURE_OPENAI_API_KEY",
        model_env="AZURE_OPENAI_REASONING_MODEL",
        legacy_model_env="AZURE_OPENAI_MODEL",
        toolcall_model_env="AZURE_OPENAI_TOOLCALL_MODEL",
        classification_model_env="AZURE_OPENAI_CLASSIFICATION_MODEL",
        endpoint_env="AZURE_OPENAI_BASE_URL",
        api_version_env="AZURE_OPENAI_API_VERSION",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="bedrock",
        label="Amazon Bedrock (IAM auth)",
        credential_kind="ambient",
        model_env="BEDROCK_REASONING_MODEL",
        toolcall_model_env="BEDROCK_TOOLCALL_MODEL",
        classification_model_env="BEDROCK_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="ollama",
        label="Ollama (local)",
        credential_kind="local",
        api_key_env="OLLAMA_HOST",
        model_env="OLLAMA_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="codex",
        label="OpenAI Codex CLI",
        credential_kind="cli",
        model_env="CODEX_MODEL",
        cli_model_env="CODEX_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="cursor",
        label="Cursor Agent CLI",
        credential_kind="cli",
        model_env="CURSOR_MODEL",
        cli_model_env="CURSOR_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="claude-code",
        label="Anthropic Claude Code CLI",
        credential_kind="cli",
        model_env="CLAUDE_CODE_MODEL",
        cli_model_env="CLAUDE_CODE_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="gemini-cli",
        label="Google Gemini CLI",
        credential_kind="cli",
        model_env="GEMINI_CLI_MODEL",
        cli_model_env="GEMINI_CLI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="antigravity-cli",
        label="Google Antigravity CLI",
        credential_kind="cli",
        model_env="ANTIGRAVITY_CLI_MODEL",
        cli_model_env="ANTIGRAVITY_CLI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="opencode",
        label="OpenCode CLI",
        credential_kind="cli",
        model_env="OPENCODE_MODEL",
        cli_model_env="OPENCODE_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="kimi",
        label="Kimi Code CLI",
        credential_kind="cli",
        model_env="KIMI_MODEL",
        cli_model_env="KIMI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="copilot",
        label="GitHub Copilot CLI",
        credential_kind="cli",
        model_env="COPILOT_MODEL",
        cli_model_env="COPILOT_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="grok-cli",
        label="xAI Grok Build CLI",
        credential_kind="cli",
        model_env="GROK_CLI_MODEL",
        cli_model_env="GROK_CLI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="pi",
        label="Pi CLI (pi.dev, BYOK multi-provider)",
        credential_kind="cli",
        model_env="PI_MODEL",
        cli_model_env="PI_MODEL",
        allow_custom_models=True,
    ),
)

PROVIDER_BY_VALUE: dict[str, ProviderSpec] = {spec.value: spec for spec in PROVIDER_SPECS}
SUPPORTED_PROVIDER_VALUES: tuple[str, ...] = tuple(spec.value for spec in PROVIDER_SPECS)
API_KEY_PROVIDER_ENVS: dict[str, str] = {
    spec.value: spec.api_key_env for spec in PROVIDER_SPECS if spec.uses_open_sre_api_key
}
KEYLESS_PROVIDER_VALUES: frozenset[str] = frozenset(
    spec.value for spec in PROVIDER_SPECS if not spec.uses_open_sre_api_key
)


def provider_spec(provider: str) -> ProviderSpec | None:
    """Return the provider spec for *provider*, if supported."""
    return PROVIDER_BY_VALUE.get(provider.strip().lower())


def require_provider_spec(provider: str) -> ProviderSpec:
    """Return the provider spec or raise ``KeyError`` for unsupported providers."""
    spec = provider_spec(provider)
    if spec is None:
        raise KeyError(provider)
    return spec


__all__ = [
    "API_KEY_PROVIDER_ENVS",
    "CredentialKind",
    "KEYLESS_PROVIDER_VALUES",
    "PROVIDER_BY_VALUE",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "SUPPORTED_PROVIDER_VALUES",
    "provider_spec",
    "require_provider_spec",
]
