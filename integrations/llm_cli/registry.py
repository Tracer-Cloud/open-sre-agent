"""Registration table for CLI-backed LLM providers (``LLM_PROVIDER`` subprocess path)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from config.llm_auth.provider_catalog import require_provider_spec
from integrations.llm_cli.base import LLMCLIAdapter


@dataclass(frozen=True)
class CLIProviderRegistration:
    """Maps a configured ``LLM_PROVIDER`` value to adapter construction + model env."""

    adapter_factory: Callable[[], LLMCLIAdapter]
    #: Optional model override env var; unset or empty → ``None`` (CLI default / omit flag).
    model_env_key: str


def _codex_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.codex import CodexAdapter

    return CodexAdapter()


def _cursor_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.cursor import CursorAdapter

    return CursorAdapter()


def _claude_code_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _gemini_cli_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.gemini_cli import GeminiCLIAdapter

    return GeminiCLIAdapter()


def _antigravity_cli_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.antigravity_cli import AntigravityCLIAdapter

    return AntigravityCLIAdapter()


def _opencode_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.opencode import OpenCodeAdapter

    return OpenCodeAdapter()


def _kimi_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.kimi import KimiAdapter

    return KimiAdapter()


def _copilot_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.copilot import CopilotAdapter

    return CopilotAdapter()


def _grok_cli_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.grok_cli import GrokCLIAdapter

    return GrokCLIAdapter()


def _pi_factory() -> LLMCLIAdapter:
    from integrations.llm_cli.pi_cli import PiAdapter

    return PiAdapter()


CLI_PROVIDER_REGISTRY: dict[str, CLIProviderRegistration] = {
    "codex": CLIProviderRegistration(
        adapter_factory=_codex_factory,
        model_env_key=require_provider_spec("codex").cli_model_env or "CODEX_MODEL",
    ),
    "cursor": CLIProviderRegistration(
        adapter_factory=_cursor_factory,
        model_env_key=require_provider_spec("cursor").cli_model_env or "CURSOR_MODEL",
    ),
    "claude-code": CLIProviderRegistration(
        adapter_factory=_claude_code_factory,
        model_env_key=require_provider_spec("claude-code").cli_model_env or "CLAUDE_CODE_MODEL",
    ),
    "gemini-cli": CLIProviderRegistration(
        adapter_factory=_gemini_cli_factory,
        model_env_key=require_provider_spec("gemini-cli").cli_model_env or "GEMINI_CLI_MODEL",
    ),
    "antigravity-cli": CLIProviderRegistration(
        adapter_factory=_antigravity_cli_factory,
        model_env_key=require_provider_spec("antigravity-cli").cli_model_env
        or "ANTIGRAVITY_CLI_MODEL",
    ),
    "opencode": CLIProviderRegistration(
        adapter_factory=_opencode_factory,
        model_env_key=require_provider_spec("opencode").cli_model_env or "OPENCODE_MODEL",
    ),
    "kimi": CLIProviderRegistration(
        adapter_factory=_kimi_factory,
        model_env_key=require_provider_spec("kimi").cli_model_env or "KIMI_MODEL",
    ),
    "copilot": CLIProviderRegistration(
        adapter_factory=_copilot_factory,
        model_env_key=require_provider_spec("copilot").cli_model_env or "COPILOT_MODEL",
    ),
    "grok-cli": CLIProviderRegistration(
        adapter_factory=_grok_cli_factory,
        model_env_key=require_provider_spec("grok-cli").cli_model_env or "GROK_CLI_MODEL",
    ),
    "pi": CLIProviderRegistration(
        adapter_factory=_pi_factory,
        model_env_key=require_provider_spec("pi").cli_model_env or "PI_MODEL",
    ),
}


def get_cli_provider_registration(provider: str) -> CLIProviderRegistration | None:
    """Return registration for *provider* if it is a registered CLI-backed LLM."""
    return CLI_PROVIDER_REGISTRY.get(provider)
