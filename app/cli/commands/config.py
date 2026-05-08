"""CLI commands for LLM/env config and local ~/.config/opensre/config.yml."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from app.constants import OPENSRE_HOME_DIR

_SUPPORTED_LAYOUTS = {"classic", "pinned"}
_SUPPORTED_KEYS = ("interactive.enabled", "interactive.layout")


def _masked(value: str | None) -> str:
    if not value:
        return "(not set)"
    return value[:4] + "****" if len(value) > 4 else "****"


def _emit_llm_config() -> None:
    """Print current LLM provider and model from environment (legacy `opensre config`)."""
    from app.cli.support.context import is_json_output

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
    click.echo("To change LLM settings, run: opensre onboard")
    click.echo("Local CLI YAML: opensre config show / opensre config set …")


def _config_path() -> Path:
    return OPENSRE_HOME_DIR / "config.yml"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}

    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not parse local config file at {path}: {exc}") from exc

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise click.ClickException(
            f"Invalid config file at {path}: expected a mapping at the top level."
        )
    return data


def _save_config(data: dict[str, Any]) -> None:
    import yaml  # type: ignore[import-untyped]

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise click.UsageError(
        "Invalid value for interactive.enabled. Use one of: true/false, 1/0, yes/no, on/off."
    )


def _coerce_value(key: str, raw_value: str) -> bool | str:
    if key == "interactive.enabled":
        return _parse_bool(raw_value)
    if key == "interactive.layout":
        layout = raw_value.strip().lower()
        if layout not in _SUPPORTED_LAYOUTS:
            raise click.UsageError(
                "Invalid value for interactive.layout. Use 'classic' or 'pinned'."
            )
        return layout
    raise click.UsageError(
        f"Unknown config key '{key}'. Supported keys: {', '.join(_SUPPORTED_KEYS)}"
    )


def _set_nested_key(data: dict[str, Any], dotted_key: str, value: Any) -> dict[str, Any]:
    head, tail = dotted_key.split(".", 1)
    node = data.get(head)
    if not isinstance(node, dict):
        node = {}
    node[tail] = value
    data[head] = node
    return data


@click.group(name="config", invoke_without_command=True)
@click.pass_context
def config_command(ctx: click.Context) -> None:
    """LLM/environment config by default; subcommands manage ~/.config/opensre/config.yml."""
    if ctx.invoked_subcommand is None:
        _emit_llm_config()


@config_command.command(name="show")
def config_show() -> None:
    """Show local ~/.config/opensre/config.yml values."""
    from app.cli.support.context import is_json_output

    payload = _load_config()

    if is_json_output():
        click.echo(json.dumps(payload))
        return

    import yaml  # type: ignore[import-untyped]

    path = _config_path()
    click.echo(f"# {path} (on-disk values; environment variables do not override this output)")
    click.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())


@config_command.command(name="set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set one local config key in ~/.config/opensre/config.yml."""
    key = key.strip()
    coerced = _coerce_value(key, value)
    data = _load_config()
    updated = _set_nested_key(data, key, coerced)
    _save_config(updated)
    click.echo(f"✓ Set {key} = {coerced}")
