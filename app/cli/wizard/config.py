"""Wizard configuration metadata."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import (
    ANTHROPIC_REASONING_MODEL,
    BEDROCK_REASONING_MODEL,
    DEEPSEEK_REASONING_MODEL,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    GEMINI_REASONING_MODEL,
    NVIDIA_REASONING_MODEL,
    OPENAI_REASONING_MODEL,
    OPENROUTER_REASONING_MODEL,
)
from app.integrations.llm_cli.base import LLMCLIAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ENV_PATH = Path(os.getenv("OPENSRE_PROJECT_ENV_PATH", PROJECT_ROOT / ".env"))

CredentialKind = Literal["api_key", "host", "cli", "none"]


@dataclass(frozen=True)
class ModelOption:
    """A selectable default model."""

    value: str
    label: str


@dataclass(frozen=True)
class ProviderOption:
    """Wizard metadata for a supported LLM provider."""

    value: str
    label: str
    group: str
    api_key_env: str
    model_env: str
    default_model: str
    models: tuple[ModelOption, ...]
    #: If set, ``sync_provider_env`` also writes this key (same value) for legacy .env files.
    legacy_model_env: str | None = None
    #: Env var that holds the *toolcall* model for this provider. ``None`` for
    #: providers that don't expose a separate toolcall model (e.g. CLI-backed
    #: providers like ``codex``/``claude-code``, or Ollama).
    toolcall_model_env: str | None = None
    #: Env var that holds the *classification* model for this provider. When
    #: unset, ``sync_provider_env`` falls back to replacing ``_REASONING_MODEL``
    #: with ``_CLASSIFICATION_MODEL`` in ``model_env``.
    classification_model_env: str | None = None
    #: Human-readable name for the credential requested during onboarding. Most
    #: providers want an API key; Ollama wants a host URL. Used as the wizard
    #: prompt label, e.g. ``{label} {credential_label} ({api_key_env})``.
    credential_label: str = "API key"
    #: Whether the credential should be prompted as a secret (hidden input).
    #: API keys are secrets; a local Ollama host URL is not.
    credential_secret: bool = True
    #: Optional hint shown as the default value in the prompt (e.g. the
    #: default Ollama host URL). Empty string means no default.
    credential_default: str = ""
    #: ``cli`` providers use ``adapter_factory`` and vendor auth (no API key in .env).
    credential_kind: CredentialKind = "api_key"
    adapter_factory: Callable[[], LLMCLIAdapter] | None = None
    #: Whether the CLI should accept model IDs outside the curated quick-pick list.
    #: Use this for providers whose model catalogs are large, account-gated, or
    #: updated independently of OpenSRE releases.
    allow_custom_models: bool = False


ANTHROPIC_MODELS = (
    ModelOption(value=ANTHROPIC_REASONING_MODEL, label="Claude Opus 4.7"),
    ModelOption(value="claude-sonnet-4-20250514", label="Claude Sonnet 4"),
)

OPENAI_MODELS = (
    ModelOption(value=OPENAI_REASONING_MODEL, label="GPT-5.4 mini"),
    ModelOption(value="gpt-5.4", label="GPT-5.4"),
    ModelOption(value="gpt-5.4-nano", label="GPT-5.4 nano"),
    ModelOption(value="gpt-5.3-codex", label="GPT-5.3-Codex"),
)

OPENROUTER_MODELS = (
    ModelOption(value=OPENROUTER_REASONING_MODEL, label="OpenRouter Auto (smart routing)"),
    ModelOption(value="openai/gpt-5.2", label="GPT-5.2 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-opus-4.6", label="Claude Opus 4.6 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-sonnet-4.5", label="Claude Sonnet 4.5 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-haiku-4.5", label="Claude Haiku 4.5 (via OpenRouter)"),
    ModelOption(
        value="google/gemini-3.1-pro-preview", label="Gemini 3.1 Pro (preview, via OpenRouter)"
    ),
    ModelOption(
        value="google/gemini-3-flash-preview", label="Gemini 3 Flash (preview, via OpenRouter)"
    ),
    ModelOption(
        value="google/gemini-3.1-flash-lite-preview",
        label="Gemini 3.1 Flash-Lite (preview, via OpenRouter)",
    ),
    ModelOption(
        value="google/gemini-3.1-flash-image-preview",
        label="Gemini 3.1 Flash Image (preview, via OpenRouter)",
    ),
    ModelOption(
        value="google/gemini-3-pro-image-preview",
        label="Gemini 3 Pro Image (preview, via OpenRouter)",
    ),
    ModelOption(value="meta-llama/llama-4-maverick", label="Llama 4 Maverick (via OpenRouter)"),
    ModelOption(value="meta-llama/llama-4-scout", label="Llama 4 Scout (via OpenRouter)"),
    ModelOption(value="mistralai/mistral-large-2512", label="Mistral Large 3 (via OpenRouter)"),
    ModelOption(value="x-ai/grok-4", label="Grok 4 (via OpenRouter)"),
    ModelOption(value="x-ai/grok-4-fast", label="Grok 4 Fast (via OpenRouter)"),
    ModelOption(value="moonshotai/kimi-k2.5", label="Kimi K2.5 (via OpenRouter)"),
    ModelOption(value="z-ai/glm-4.7", label="GLM 4.7 (via OpenRouter)"),
    ModelOption(value="minimax/minimax-m2", label="MiniMax M2 (via OpenRouter)"),
    ModelOption(value="deepseek/deepseek-v3.2", label="DeepSeek V3.2 (via OpenRouter)"),
    ModelOption(value="qwen/qwen-3.6-plus-preview", label="Qwen 3.6 Plus (via OpenRouter)"),
)

DEEPSEEK_MODELS = (
    ModelOption(value=DEEPSEEK_REASONING_MODEL, label="DeepSeek V4 Pro"),
    ModelOption(value="deepseek-v4-flash", label="DeepSeek V4 Flash"),
)

GEMINI_MODELS = (
    ModelOption(value=GEMINI_REASONING_MODEL, label="Gemini 3.1 Pro (preview)"),
    ModelOption(value="gemini-3-flash-preview", label="Gemini 3 Flash (preview)"),
    ModelOption(value="gemini-3.1-flash-lite-preview", label="Gemini 3.1 Flash-Lite (preview)"),
    ModelOption(value="gemini-3.1-flash-image-preview", label="Gemini 3.1 Flash Image (preview)"),
    ModelOption(value="gemini-3-pro-image-preview", label="Gemini 3 Pro Image (preview)"),
)

NVIDIA_MODELS = (
    ModelOption(
        value=NVIDIA_REASONING_MODEL,
        label="Nemotron 3 Super 120B (5x higher throughput for agentic AI)",
    ),
    ModelOption(value="nvidia/nemotron-3-nano-30b-a3b", label="Nemotron 3 Nano 30B"),
)

BEDROCK_MODELS = (
    ModelOption(
        value=BEDROCK_REASONING_MODEL,
        label="Claude Sonnet 4.6 (US cross-region) — default",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-7",
        label="Claude Opus 4.7 (US cross-region) — most capable",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-6-v1",
        label="Claude Opus 4.6 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-5-20251101-v1:0",
        label="Claude Opus 4.5 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-1-20250805-v1:0",
        label="Claude Opus 4.1 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        label="Claude Sonnet 4.5 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-sonnet-4-20250514-v1:0",
        label="Claude Sonnet 4 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        label="Claude Haiku 4.5 (US cross-region) — fast, cost-efficient",
    ),
    ModelOption(
        value="us.meta.llama4-maverick-17b-instruct-v1:0",
        label="Llama 4 Maverick 17B (US cross-region)",
    ),
    ModelOption(
        value="us.amazon.nova-pro-v1:0",
        label="Amazon Nova Pro (US cross-region)",
    ),
    ModelOption(
        value="mistral.mistral-large-3-675b-instruct",
        label="Mistral Large 3 675B Instruct (on-demand)",
    ),
)

OLLAMA_MODELS = (
    ModelOption(value="llama3.2", label="Llama 3.2 (3B) — recommended"),
    ModelOption(value="llama3.1:8b", label="Llama 3.1 (8B)"),
    ModelOption(value="mistral", label="Mistral 7B"),
    ModelOption(value="qwen2.5:7b", label="Qwen 2.5 (7B)"),
)

# Source: https://platform.claude.com/docs/en/about-claude/models/overview (verified 2026-05-21).
# Empty value means "no --model" so Claude Code uses its configured default.
CLAUDE_CODE_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Claude Code configured model)",
    ),
    ModelOption(value="claude-opus-4-7", label="Claude Opus 4.7 — most capable"),
    ModelOption(value="claude-sonnet-4-6", label="Claude Sonnet 4.6 — balanced"),
    ModelOption(value="claude-haiku-4-5", label="Claude Haiku 4.5 — fast, cost-efficient"),
)

# Source: https://developers.openai.com/codex/cli/features (verified 2026-05-21).
# Empty value means "no -m" so the Codex CLI uses its configured default/current model.
CODEX_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no -m; use Codex configured model)",
    ),
    ModelOption(value="gpt-5.5", label="gpt-5.5 — newest frontier coding"),
    ModelOption(value="gpt-5.4", label="gpt-5.4 — fallback default"),
    ModelOption(value="gpt-5.4-mini", label="gpt-5.4-mini — fast, cost-efficient"),
    ModelOption(value="gpt-5.3-codex", label="gpt-5.3-codex — coding-optimized"),
    ModelOption(
        value="gpt-5.3-codex-spark",
        label="gpt-5.3-codex-spark — research preview (ChatGPT Pro)",
    ),
)

# Source: google-gemini/gemini-cli, packages/core/src/config/models.ts (verified 2026-05-21).
# Empty value means "no --model" so Gemini CLI uses its configured/default model.
GEMINI_CLI_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Gemini CLI configured model)",
    ),
    ModelOption(
        value="gemini-3.1-pro-preview",
        label="gemini-3.1-pro-preview — newest frontier (preview)",
    ),
    ModelOption(
        value="gemini-3-flash-preview",
        label="gemini-3-flash-preview — fast (preview)",
    ),
    ModelOption(
        value="gemini-3.1-flash-lite-preview",
        label="gemini-3.1-flash-lite-preview — fastest (preview)",
    ),
    ModelOption(
        value="gemini-2.5-pro",
        label="gemini-2.5-pro — stable, strongest reasoning",
    ),
    ModelOption(
        value="gemini-2.5-flash",
        label="gemini-2.5-flash — stable, balanced",
    ),
    ModelOption(
        value="gemini-2.5-flash-lite",
        label="gemini-2.5-flash-lite — stable, fastest",
    ),
)

# Source: https://opencode.ai/docs/zen (verified 2026-05-21).
# OpenCode routes models through OpenCode Zen using the ``opencode/`` prefix.
# Curated subset of the full ~40-model catalog; the wizard's custom-ID escape
# hatch covers anything not pre-listed here.
OPENCODE_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no -m; use OpenCode configured model)",
    ),
    ModelOption(value="opencode/gpt-5.5", label="GPT-5.5 (OpenCode Zen) — frontier"),
    ModelOption(value="opencode/gpt-5.4", label="GPT-5.4 (OpenCode Zen)"),
    ModelOption(value="opencode/gpt-5.4-mini", label="GPT-5.4 mini (OpenCode Zen) — fast"),
    ModelOption(
        value="opencode/gpt-5.3-codex",
        label="GPT-5.3 Codex (OpenCode Zen) — coding-optimized",
    ),
    ModelOption(
        value="opencode/gpt-5.3-codex-spark",
        label="GPT-5.3 Codex Spark (OpenCode Zen) — research preview",
    ),
    ModelOption(
        value="opencode/claude-opus-4-7",
        label="Claude Opus 4.7 (OpenCode Zen) — most capable",
    ),
    ModelOption(
        value="opencode/claude-sonnet-4-6",
        label="Claude Sonnet 4.6 (OpenCode Zen) — balanced",
    ),
    ModelOption(
        value="opencode/claude-haiku-4-5",
        label="Claude Haiku 4.5 (OpenCode Zen) — fast",
    ),
    ModelOption(value="opencode/gemini-3.1-pro", label="Gemini 3.1 Pro (OpenCode Zen)"),
    ModelOption(value="opencode/gemini-3-flash", label="Gemini 3 Flash (OpenCode Zen)"),
    ModelOption(value="opencode/kimi-k2.6", label="Kimi K2.6 (OpenCode Zen)"),
    ModelOption(value="opencode/minimax-m2.7", label="MiniMax M2.7 (OpenCode Zen)"),
    ModelOption(value="opencode/qwen3.6-plus", label="Qwen3.6 Plus (OpenCode Zen)"),
    ModelOption(value="opencode/glm-5.1", label="GLM 5.1 (OpenCode Zen)"),
    ModelOption(
        value="opencode/minimax-m2.5-free",
        label="MiniMax M2.5 (OpenCode Zen) — free tier",
    ),
    ModelOption(
        value="opencode/deepseek-v4-flash-free",
        label="DeepSeek V4 Flash (OpenCode Zen) — free tier",
    ),
)


CURSOR_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Cursor configured model)",
    ),
    ModelOption(value="auto", label="auto"),
    ModelOption(value="gpt-5", label="gpt-5"),
    ModelOption(value="sonnet-4", label="sonnet-4"),
    ModelOption(value="sonnet-4-thinking", label="sonnet-4-thinking"),
)


def _codex_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.codex import CodexAdapter

    return CodexAdapter()


def _cursor_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.cursor import CursorAdapter

    return CursorAdapter()


def _claude_code_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _gemini_cli_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.gemini_cli import GeminiCLIAdapter

    return GeminiCLIAdapter()


def _opencode_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.opencode import OpenCodeAdapter

    return OpenCodeAdapter()


def _kimi_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.kimi import KimiAdapter

    return KimiAdapter()


def _copilot_adapter_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.copilot import CopilotAdapter

    return CopilotAdapter()


KIMI_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no -m; use Kimi configured model)",
    ),
    ModelOption(value="kimi-k2-thinking-turbo", label="kimi-k2-thinking-turbo"),
    ModelOption(value="kimi-k2.5", label="kimi-k2.5"),
    ModelOption(value="kimi-k2.6", label="kimi-k2.6"),
)


# Empty value means "no --model" so Copilot CLI uses its configured default model.
# We do not hardcode model identifiers here: the Copilot CLI's accepted --model
# values are not stable across releases and live behind GitHub-side gating, so
# baking them in risks "model not found" errors after the user has finished the
# wizard. Users override via COPILOT_MODEL when they know what their plan exposes.
COPILOT_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Copilot CLI configured model)",
    ),
)


SUPPORTED_PROVIDERS = (
    ProviderOption(
        value="anthropic",
        label="Anthropic",
        group="Hosted providers",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_REASONING_MODEL",
        default_model=ANTHROPIC_REASONING_MODEL,
        models=ANTHROPIC_MODELS,
        legacy_model_env="ANTHROPIC_MODEL",
        toolcall_model_env="ANTHROPIC_TOOLCALL_MODEL",
        classification_model_env="ANTHROPIC_CLASSIFICATION_MODEL",
    ),
    ProviderOption(
        value="openai",
        label="OpenAI",
        group="Hosted providers",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_REASONING_MODEL",
        default_model=OPENAI_REASONING_MODEL,
        models=OPENAI_MODELS,
        legacy_model_env="OPENAI_MODEL",
        toolcall_model_env="OPENAI_TOOLCALL_MODEL",
        classification_model_env="OPENAI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="openrouter",
        label="OpenRouter",
        group="Hosted providers",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_REASONING_MODEL",
        default_model=OPENROUTER_REASONING_MODEL,
        models=OPENROUTER_MODELS,
        legacy_model_env="OPENROUTER_MODEL",
        toolcall_model_env="OPENROUTER_TOOLCALL_MODEL",
        classification_model_env="OPENROUTER_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="deepseek",
        label="DeepSeek",
        group="Hosted providers",
        api_key_env="DEEPSEEK_API_KEY",
        model_env="DEEPSEEK_REASONING_MODEL",
        default_model=DEEPSEEK_REASONING_MODEL,
        models=DEEPSEEK_MODELS,
        legacy_model_env="DEEPSEEK_MODEL",
        toolcall_model_env="DEEPSEEK_TOOLCALL_MODEL",
        classification_model_env="DEEPSEEK_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="gemini",
        label="Google Gemini",
        group="Hosted providers",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_REASONING_MODEL",
        default_model=GEMINI_REASONING_MODEL,
        models=GEMINI_MODELS,
        legacy_model_env="GEMINI_MODEL",
        toolcall_model_env="GEMINI_TOOLCALL_MODEL",
        classification_model_env="GEMINI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="nvidia",
        label="NVIDIA NIM",
        group="Hosted providers",
        api_key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_REASONING_MODEL",
        default_model=NVIDIA_REASONING_MODEL,
        models=NVIDIA_MODELS,
        legacy_model_env="NVIDIA_MODEL",
        toolcall_model_env="NVIDIA_TOOLCALL_MODEL",
        classification_model_env="NVIDIA_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="bedrock",
        label="Amazon Bedrock (IAM auth)",
        group="Hosted providers",
        # Intentionally empty: Bedrock authenticates via the IAM credential
        # chain (env, ~/.aws/credentials, instance profile) — no API key to
        # prompt for.  Empty string is safe: every downstream check uses
        # ``bool(provider.api_key_env)`` or ``.get()`` (never subscript).
        api_key_env="",
        model_env="BEDROCK_REASONING_MODEL",
        default_model=BEDROCK_REASONING_MODEL,
        models=BEDROCK_MODELS,
        toolcall_model_env="BEDROCK_TOOLCALL_MODEL",
        classification_model_env="BEDROCK_CLASSIFICATION_MODEL",
        credential_label="AWS region (uses IAM credentials)",
        credential_secret=False,
        # credential_kind="none" causes flow.py to skip the credential prompt
        # entirely.  Region is picked up from AWS_DEFAULT_REGION / ~/.aws/config.
        credential_kind="none",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="codex",
        label="OpenAI Codex CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CODEX_MODEL",
        default_model="",
        models=CODEX_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_codex_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="cursor",
        label="Cursor Agent CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CURSOR_MODEL",
        default_model="auto",
        models=CURSOR_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_cursor_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="claude-code",
        label="Anthropic Claude Code CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CLAUDE_CODE_MODEL",
        default_model="",
        models=CLAUDE_CODE_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_claude_code_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="gemini-cli",
        label="Google Gemini CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="GEMINI_CLI_MODEL",
        default_model="",
        models=GEMINI_CLI_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_gemini_cli_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="opencode",
        label="OpenCode CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="OPENCODE_MODEL",
        default_model="",
        models=OPENCODE_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_opencode_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="kimi",
        label="Kimi Code CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="KIMI_MODEL",
        default_model="",
        models=KIMI_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_kimi_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="copilot",
        label="GitHub Copilot CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="COPILOT_MODEL",
        default_model="",
        models=COPILOT_MODELS,
        credential_kind="cli",
        credential_secret=False,
        adapter_factory=_copilot_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="ollama",
        label="Ollama (local)",
        group="Local providers",
        api_key_env="OLLAMA_HOST",
        model_env="OLLAMA_MODEL",
        default_model=DEFAULT_OLLAMA_MODEL,
        models=OLLAMA_MODELS,
        credential_label="host URL",
        credential_secret=False,
        credential_default=DEFAULT_OLLAMA_HOST,
        allow_custom_models=True,
    ),
)

PROVIDER_BY_VALUE = {provider.value: provider for provider in SUPPORTED_PROVIDERS}
