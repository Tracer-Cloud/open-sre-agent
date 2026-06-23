"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import os
import sys
from typing import Literal

import questionary as questionary
from rich.text import Text

from app.cli.interactive_shell.ui.theme import (
    ERROR,
    GLYPH_ERROR,
    GLYPH_WARNING,
    SECONDARY,
    TEXT,
    WARNING,
)
from app.cli.wizard._integration_configurators import (
    DEFAULT_GITHUB_MCP_MODE as DEFAULT_GITHUB_MCP_MODE,
)
from app.cli.wizard._integration_configurators import (
    DEFAULT_GITHUB_MCP_URL as DEFAULT_GITHUB_MCP_URL,
)
from app.cli.wizard._integration_configurators import (
    _configure_selected_integrations,
)
from app.cli.wizard._ui import (
    Choice,
    _choose,
    _choose_model,
    _confirm,
    _console,
    _local_defaults,
    _persist_llm_api_key,
    _prompt_value,
    _render_header,
    _render_next_steps,
    _render_saved_summary,
    _select_target_for_advanced,
    _step,
    _step_header,
)
from app.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env
from app.cli.wizard.integration_health import IntegrationHealthResult as IntegrationHealthResult
from app.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from app.cli.wizard.store import get_store_path, save_local_config
from app.cli.wizard.validation import build_demo_action_response as _build_demo_action_response
from app.integrations.llm_cli.binary_resolver import diagnose_binary_path

WIZARD_TOTAL_STEPS = 4


# Re-export build_demo_action_response from validation as a stable module-level
# attribute. The wrapper indirection (instead of `from x import y`) is
# preserved so the function remains patchable via monkeypatch.setattr(flow,
# "build_demo_action_response", ...) — but we also keep the underlying import
# at module load time so the attribute exists immediately, even in CI parallel
# test workers where lazy imports inside the wrapper occasionally fail to
# materialize on first access.
def build_demo_action_response():
    return _build_demo_action_response()


def _credential_line_for_saved_summary(provider: ProviderOption) -> str:
    """One-line credential description for the post-wizard saved summary."""
    if provider.credential_kind != "cli":
        return "system keychain"
    if provider.adapter_factory is None:
        return f"{provider.label} (CLI)"
    cli_adapter = provider.adapter_factory()
    return f"{provider.label} ({cli_adapter.auth_hint})"


def _run_cli_llm_onboarding(provider: ProviderOption) -> Literal["ok", "abort", "repick"]:
    """Probe CLI binary + auth; recovery menu when missing. ``repick`` = choose another LLM."""
    factory = provider.adapter_factory
    if factory is None:
        _console.print(
            f"[{ERROR}]  {GLYPH_ERROR}  Internal error: CLI provider missing adapter factory.[/]"
        )
        return "abort"
    adapter = factory()
    env_key = adapter.binary_env_key
    install_hint = adapter.install_hint
    auth_hint = adapter.auth_hint
    name = adapter.name
    for _attempt in range(10):
        probe = adapter.detect()
        if probe.installed and probe.logged_in is True:
            _console.print(f"[{SECONDARY}]{probe.detail}[/]")
            return "ok"
        if probe.installed and probe.logged_in is not True:
            _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
            status_prompt = (
                f"{provider.label} requires login. What next?"
                if probe.logged_in is False
                else f"Could not verify {provider.label} login. What next?"
            )
            action = _choose(
                status_prompt,
                [
                    Choice(
                        value="retry",
                        label="Re-detect after logging in",
                        hint=auth_hint,
                    ),
                    Choice(
                        value="repick",
                        label="Pick a different LLM provider",
                        hint=None,
                    ),
                ],
                default="retry",
            )
            if action == "repick":
                return "repick"
            continue
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
        action = _choose(
            f"{provider.label} not found. What next?",
            [
                Choice(
                    value="retry",
                    label="Re-detect after install",
                    hint=install_hint,
                ),
                Choice(
                    value="path",
                    label="Enter full path to the binary",
                    hint=f"Writes {env_key} to .env",
                ),
                Choice(
                    value="repick",
                    label="Pick a different LLM provider",
                    hint=None,
                ),
            ],
            default="retry",
        )
        if action == "repick":
            return "repick"
        if action == "path":
            path = _prompt_value(f"Full path to {name} binary")
            reason = diagnose_binary_path(path)
            if reason:
                _console.print(f"[{WARNING}]{reason} Try again.[/]")
                continue
            sync_env_values({env_key: path})
            os.environ[env_key] = path
            continue
        _console.print(f"[{SECONDARY}]    Hint: {install_hint}[/]")
    _console.print(f"[{WARNING}]  {GLYPH_WARNING}  Too many retry attempts. Aborting setup.[/]")
    return "abort"


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive wizard."""
    _render_header()
    defaults = _local_defaults()
    saved_provider_value = defaults["provider"] if isinstance(defaults["provider"], str) else None
    saved_model_value = defaults["model"] if isinstance(defaults["model"], str) else ""
    default_wizard_mode = (
        defaults["wizard_mode"] if isinstance(defaults["wizard_mode"], str) else "quickstart"
    )
    default_provider_value = (
        saved_provider_value
        if saved_provider_value in PROVIDER_BY_VALUE
        else SUPPORTED_PROVIDERS[0].value
    )

    _step_header(1, WIZARD_TOTAL_STEPS, "Setup Mode")
    wizard_mode = _choose(
        "How do you want to get started?",
        [
            Choice(
                value="quickstart", label="Quickstart", hint="Local setup with the usual defaults"
            ),
            Choice(
                value="advanced",
                label="Advanced",
                hint="Show probes and choose the target explicitly",
            ),
        ],
        default=default_wizard_mode,
    )

    store_path = get_store_path()
    local_probe = probe_local_target(store_path)
    remote_probe = ProbeResult(
        target="remote",
        reachable=False,
        detail="Remote probing is shown during Advanced setup.",
    )

    if wizard_mode == "advanced":
        remote_probe = probe_remote_target()
        target = _select_target_for_advanced(local_probe, remote_probe)
        if target is None:
            return 1
    else:
        target = "local"

    if target != "local":
        print("Only local configuration is supported today.", file=sys.stderr)
        return 1

    force_repick = False
    provider: ProviderOption
    model: str
    while True:
        _step_header(2, WIZARD_TOTAL_STEPS, "LLM Provider")
        saved_provider = (
            PROVIDER_BY_VALUE.get(saved_provider_value) if saved_provider_value else None
        )
        if saved_provider is not None and not force_repick:
            current_model = saved_model_value or saved_provider.default_model
            _console.print(
                f"[{SECONDARY}]current provider  {saved_provider.label}  ·  {current_model}[/]"
            )
            change_provider = _confirm("Change provider?", default=False)
        else:
            change_provider = True
        force_repick = False

        if change_provider:
            provider = PROVIDER_BY_VALUE[
                _choose(
                    "Choose your LLM provider",
                    [
                        Choice(
                            value=p.value,
                            label=p.label,
                            hint=p.group,
                        )
                        for p in SUPPORTED_PROVIDERS
                    ],
                    default=default_provider_value,
                )
            ]
            model = provider.default_model
            if provider.credential_kind not in ("cli", "none"):
                _step(provider.credential_label.title())
                try:
                    api_key = _prompt_value(
                        f"{provider.label} {provider.credential_label} ({provider.api_key_env})",
                        default=provider.credential_default,
                        secret=provider.credential_secret,
                    )
                except KeyboardInterrupt:
                    _console.print(f"\n[{WARNING}]Setup cancelled.[/]")
                    return 1
                if not _persist_llm_api_key(provider.api_key_env, api_key):
                    return 1
        else:
            assert saved_provider is not None
            provider = saved_provider
            model = saved_model_value or provider.default_model
            if provider.credential_kind not in ("cli", "none"):
                has_api_key = bool(defaults["has_api_key"])
                legacy_api_key = str(defaults["legacy_api_key"] or "").strip()
                if not has_api_key and legacy_api_key:
                    if not _persist_llm_api_key(provider.api_key_env, legacy_api_key):
                        return 1
                    has_api_key = True
                if not has_api_key:
                    _step(provider.credential_label.title())
                    try:
                        api_key = _prompt_value(
                            f"{provider.label} {provider.credential_label} ({provider.api_key_env})",
                            default=provider.credential_default,
                            secret=provider.credential_secret,
                        )
                    except KeyboardInterrupt:
                        _console.print(f"\n[{WARNING}]Setup cancelled.[/]")
                        return 1
                    if not _persist_llm_api_key(provider.api_key_env, api_key):
                        return 1

        if change_provider:
            model = _choose_model(provider, default=model)
        elif provider.models:
            current_display = model or "CLI default"
            _console.print(f"[{SECONDARY}]current model  {current_display}[/]")
            if _confirm("Change model?", default=False):
                model = _choose_model(provider, default=model)

        if provider.credential_kind == "cli":
            cli_out = _run_cli_llm_onboarding(provider)
            if cli_out == "abort":
                return 1
            if cli_out == "repick":
                force_repick = True
                continue
        break

    probes = {
        "local": local_probe.as_dict(),
        "remote": remote_probe.as_dict(),
    }
    saved_path = save_local_config(
        wizard_mode=wizard_mode,
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env,
        model_env=provider.model_env,
        probes=probes,
    )
    env_path = sync_provider_env(provider=provider, model=model)

    _step_header(3, WIZARD_TOTAL_STEPS, "Integrations")
    try:
        configured_integrations, integration_env_path = _configure_selected_integrations()
    except KeyboardInterrupt:
        cancelled = Text()
        cancelled.append(f"\n  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
        cancelled.append("Integration setup cancelled. AI config was kept.", style=TEXT)
        _console.print(cancelled)
        configured_integrations = []
        integration_env_path = None

    summary_env_path = integration_env_path or str(env_path)

    _step_header(4, WIZARD_TOTAL_STEPS, "Summary")
    _render_saved_summary(
        provider_label=provider.label,
        model=model,
        saved_path=str(saved_path),
        env_path=summary_env_path,
        configured_integrations=configured_integrations,
        credential_line=_credential_line_for_saved_summary(provider),
    )
    _render_next_steps()
    return 0
