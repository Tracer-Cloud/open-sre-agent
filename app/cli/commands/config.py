"""Config command — show current LLM and environment settings."""

from __future__ import annotations

import os

import click

from app.cli.support.context import is_json_output


def _masked(value: str | None) -> str:
    if not value:
        return "(not set)"
    return value[:4] + "****" if len(value) > 4 else "****"


@click.command(name="config")
def config_command() -> None:
    """Show current LLM provider and model configuration.

    To change settings, run: opensre onboard
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"

    key_env_by_provider: dict[str, str] = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "requesty": "REQUESTY_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "bedrock": "AWS_DEFAULT_REGION",
        "minimax": "MINIMAX_API_KEY",
        "ollama": "OLLAMA_HOST",
    }
    model_env_by_provider: dict[str, str] = {
        "anthropic": "ANTHROPIC_REASONING_MODEL",
        "openai": "OPENAI_REASONING_MODEL",
        "openrouter": "OPENROUTER_REASONING_MODEL",
        "requesty": "REQUESTY_REASONING_MODEL",
        "gemini": "GEMINI_REASONING_MODEL",
        "nvidia": "NVIDIA_REASONING_MODEL",
        "bedrock": "BEDROCK_MODEL",
        "minimax": "MINIMAX_REASONING_MODEL",
        "ollama": "OLLAMA_MODEL",
    }

    key_env = key_env_by_provider.get(provider, "")
    key_value = os.getenv(key_env, "") if key_env else ""
    model_env = model_env_by_provider.get(provider, "")
    model_value = os.getenv(model_env, "") if model_env else ""

    if is_json_output():
        import json

        click.echo(
            json.dumps(
                {
                    "provider": provider,
                    "model": model_value or None,
                    "api_key_set": bool(key_value),
                }
            )
        )
        return

    click.echo(f"Provider : {provider}")
    if model_value:
        click.echo(f"Model    : {model_value}")
    if key_env:
        click.echo(f"{key_env:<16}: {_masked(key_value)}")
    click.echo()
    click.echo("To change settings, run: opensre onboard")
