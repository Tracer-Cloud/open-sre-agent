"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import logging
import os
import sys
from typing import Literal

import questionary
from rich.text import Text

import surfaces.cli.wizard._integration_configurators as _integration_configurators_module
from config.env_file import sync_env_values
from config.setup_store import get_store_path, save_local_config
from core.llm.providers.azure_openai import is_azure_openai_provider
from infrastructure.terminal.theme import (
    ERROR,
    GLYPH_ERROR,
    GLYPH_WARNING,
    SECONDARY,
    TEXT,
    WARNING,
)
from integrations.llm_cli import diagnose_binary_path
from surfaces.cli.wizard.azure_openai import (
    choose_provider_model,
)
from surfaces.cli.wizard.components import (
    Choice,
    WizardBack,
    choose,
    confirm,
    console,
    local_defaults,
    prompt_value,
    select_target_for_advanced,
    step_header,
)
from surfaces.cli.wizard.configurators.github import (
    DEFAULT_GITHUB_MCP_MODE,
    DEFAULT_GITHUB_MCP_URL,
)
from surfaces.cli.wizard.custom_endpoints import (
    onboarding_provider_choices,
    onboarding_provider_default,
    resolve_onboarding_provider,
)
from surfaces.cli.wizard.endpoint_prompt import (
    ensure_endpoint_settings as ensure_provider_endpoint_settings,
)
from surfaces.cli.wizard.integration_health import IntegrationHealthResult
from surfaces.cli.wizard.llm_credential import (
    CANCEL,
    DEFERRED,
    OK,
    REPICK,
    UNSAVED,
    UNVERIFIED,
    CredentialState,
    _credential_line_for_saved_summary,
    _persist_llm_credential_with_recovery,
    _prompt_validated_llm_credential,
    _provider_choice_label,
)
from surfaces.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from surfaces.cli.wizard.summaries import (
    render_header,
    render_next_steps,
    render_saved_summary,
)
from surfaces.shared.llm_setup.catalog import (
    PROVIDER_BY_VALUE,
    SUPPORTED_PROVIDERS,
    ProviderOption,
    WizardCredentialKind,
)
from surfaces.shared.llm_setup.env_sync import sync_provider_env

WIZARD_TOTAL_STEPS = 4
logger = logging.getLogger(__name__)

#: Vendor-CLI providers a user can only reach by setting ``LLM_PROVIDER``
#: directly; onboarding no longer offers a subscription/OAuth login for them.
_HIDDEN_ONBOARDING_PROVIDERS = frozenset({"codex", "claude-code"})

__all__ = [
    "DEFAULT_GITHUB_MCP_MODE",
    "DEFAULT_GITHUB_MCP_URL",
    "IntegrationHealthResult",
    "build_demo_action_response",
    "questionary",
]


def build_demo_action_response() -> dict:
    """Return a safe built-in action response for onboarding."""
    from tools.system.sre_guidance_tool import get_sre_guidance

    return get_sre_guidance(topic="recovery_remediation", max_topics=1)


def _seed_onboarding_loops() -> int:
    """Seed starter scheduled loops after onboarding completes."""
    from infrastructure.scheduling.scheduler.loops import seed_starter_loops

    try:
        return len(seed_starter_loops())
    except Exception:
        logger.debug("Failed to seed onboarding starter loops", exc_info=True)
        return 0


def _onboarding_provider_options() -> tuple[ProviderOption, ...]:
    return tuple(
        provider
        for provider in SUPPORTED_PROVIDERS
        if provider.value not in _HIDDEN_ONBOARDING_PROVIDERS
    )


def _run_cli_llm_onboarding(provider: ProviderOption) -> Literal["ok", "abort", "repick"]:
    """Probe CLI binary + auth; recovery menu when missing. ``repick`` = choose another LLM."""
    factory = provider.adapter_factory
    if factory is None:
        console.print(
            f"[{ERROR}]  {GLYPH_ERROR}  Internal error: CLI provider missing adapter factory.[/]"
        )
        return "abort"
    adapter = factory()
    env_key = adapter.binary_env_key
    install_hint = adapter.install_hint
    auth_hint = adapter.auth_hint
    name = adapter.name
    provider_label = provider.label
    for _attempt in range(10):
        probe = adapter.detect()
        if probe.installed and probe.logged_in is True:
            console.print(f"[{SECONDARY}]{probe.detail}[/]")
            return "ok"
        if probe.installed and probe.logged_in is not True:
            console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
            status_prompt = (
                f"{provider_label} requires login. What next?"
                if probe.logged_in is False
                else f"Could not verify {provider_label} login. What next?"
            )
            action = choose(
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
        console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
        action = choose(
            f"{provider_label} not found. What next?",
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
            path = prompt_value(f"Full path to {name} binary")
            reason = diagnose_binary_path(path)
            if reason:
                console.print(f"[{WARNING}]{reason} Try again.[/]")
                continue
            sync_env_values({env_key: path})
            os.environ[env_key] = path
            continue
        console.print(f"[{SECONDARY}]    Hint: {install_hint}[/]")
    console.print(f"[{WARNING}]  {GLYPH_WARNING}  Too many retry attempts. Aborting setup.[/]")
    return "abort"


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive wizard."""
    render_header()
    defaults = local_defaults()
    saved_provider_value = defaults["provider"] if isinstance(defaults["provider"], str) else None
    saved_model_value = defaults["model"] if isinstance(defaults["model"], str) else ""
    default_wizard_mode = (
        defaults["wizard_mode"] if isinstance(defaults["wizard_mode"], str) else "quickstart"
    )
    provider_options = _onboarding_provider_options()
    provider_option_values = {p.value for p in provider_options}
    default_provider_value = (
        saved_provider_value
        if saved_provider_value in provider_option_values
        else provider_options[0].value
    )

    step_header(1, WIZARD_TOTAL_STEPS, "Setup Mode")
    wizard_mode = choose(
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
        default=default_wizard_mode
        if default_wizard_mode in {"quickstart", "advanced"}
        else "quickstart",
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
        target = select_target_for_advanced(local_probe, remote_probe)
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
    provider_extra_env: dict[str, str] = {}
    credential_state: CredentialState = OK
    # Records a ``continue_unsaved`` secret export so it can be re-applied
    # before the in-process shell handoff.
    session_env_sink: dict[str, str] = {}
    while True:
        credential_state = OK
        session_env_sink = {}
        step_header(2, WIZARD_TOTAL_STEPS, "LLM Provider")
        saved_provider = (
            PROVIDER_BY_VALUE.get(saved_provider_value) if saved_provider_value else None
        )
        if saved_provider is not None and not force_repick:
            current_model = saved_model_value or saved_provider.default_model
            console.print(
                f"[{SECONDARY}]current provider  {_provider_choice_label(saved_provider)}  ·  {current_model}[/]"
            )
            change_provider = confirm("Change provider?", default=False)
        else:
            change_provider = True
        force_repick = False

        if change_provider:
            try:
                provider_selection = choose(
                    "Choose your LLM provider",
                    onboarding_provider_choices(
                        [
                            Choice(
                                value=p.value,
                                label=_provider_choice_label(p),
                                hint=p.group,
                            )
                            for p in provider_options
                        ]
                    ),
                    default=onboarding_provider_default(default_provider_value),
                )
                provider = PROVIDER_BY_VALUE[
                    resolve_onboarding_provider(
                        provider_selection,
                        default=default_provider_value,
                    )
                ]
            except WizardBack:
                force_repick = True
                continue
            model = provider.default_model
        else:
            assert saved_provider is not None
            provider = saved_provider
            model = saved_model_value or provider.default_model

        # The model pick comes BEFORE the credential block, in both branches: the live
        # probe must run against the model that actually gets persisted. Probing the
        # provider default instead locks out anyone who picks a non-default model — an
        # Ollama user selecting a model they have pulled would be told to pull the
        # default model they never chose, on every retry.
        #
        # Azure is the exception: its "model" is a live deployment name discovered from
        # the resource, so it needs the endpoint + key first. The deployment pick is
        # therefore deferred into ``_prompt_validated_llm_credential`` (still before the
        # validation probe), and skipped here.
        if change_provider:
            if not is_azure_openai_provider(provider.value):
                try:
                    model = choose_provider_model(
                        provider,
                        default=model,
                        prompt_label=_provider_choice_label(provider),
                        back_on_cancel=True,
                    )
                except WizardBack:
                    force_repick = True
                    continue
        elif provider.models:
            current_display = model or "CLI default"
            console.print(f"[{SECONDARY}]current model  {current_display}[/]")
            if confirm("Change model?", default=False):
                model = choose_provider_model(
                    provider,
                    default=model,
                    prompt_label=_provider_choice_label(provider),
                )

        if change_provider:
            if provider.credential_kind not in (
                WizardCredentialKind.CLI,
                WizardCredentialKind.NONE,
            ):
                credential_outcome, model = _prompt_validated_llm_credential(
                    provider,
                    model=model,
                    session_env_sink=session_env_sink,
                )
                if credential_outcome == CANCEL:
                    return 1
                if credential_outcome == REPICK:
                    force_repick = True
                    continue
                if credential_outcome == DEFERRED:
                    # No key to hand: finish onboarding with the provider chosen
                    # and nothing persisted, rather than ending the wizard.
                    credential_state = DEFERRED
                elif credential_outcome == UNVERIFIED:
                    credential_state = UNVERIFIED
                elif credential_outcome == UNSAVED:
                    credential_state = UNSAVED
                # Called again here (Azure only) to populate ``provider_extra_env`` for
                # ``sync_provider_env`` below. ``_prompt_validated_llm_credential`` already
                # set ``AZURE_OPENAI_BASE_URL`` in ``os.environ`` so the probe could read it,
                # so this call short-circuits on the configured endpoint rather than
                # re-prompting — its only job now is to hand the endpoint env back for the
                # .env sync.
                azure_env = ensure_provider_endpoint_settings(provider)
                if azure_env is None:
                    force_repick = True
                    continue
                provider_extra_env = azure_env
                os.environ.update(azure_env)
        else:
            if provider.credential_kind not in (
                WizardCredentialKind.CLI,
                WizardCredentialKind.NONE,
            ):
                has_api_key = bool(defaults["has_api_key"])
                legacy_api_key = str(defaults["legacy_api_key"] or "").strip()
                # A ``host`` credential (e.g. the Ollama host) is not a secret api key: never
                # migrate a stale legacy ``api_key`` value into it — that would leak a
                # secret-shaped value into .env and point the runtime at a bogus host. Fall
                # through to the host prompt instead.
                if (
                    not has_api_key
                    and legacy_api_key
                    and provider.credential_kind != WizardCredentialKind.HOST
                ):
                    migration_outcome = _persist_llm_credential_with_recovery(
                        provider, legacy_api_key, session_env_sink=session_env_sink
                    )
                    if migration_outcome == CANCEL:
                        return 1
                    if migration_outcome == REPICK:
                        force_repick = True
                        continue
                    if migration_outcome == UNSAVED:
                        credential_state = UNSAVED
                    has_api_key = True
                if not has_api_key:
                    credential_outcome, model = _prompt_validated_llm_credential(
                        provider,
                        model=model,
                        session_env_sink=session_env_sink,
                    )
                    if credential_outcome == CANCEL:
                        return 1
                    if credential_outcome == REPICK:
                        force_repick = True
                        continue
                    if credential_outcome == DEFERRED:
                        credential_state = DEFERRED
                    elif credential_outcome == UNVERIFIED:
                        credential_state = UNVERIFIED
                    elif credential_outcome == UNSAVED:
                        credential_state = UNSAVED
            # Called again here (Azure only) to populate ``provider_extra_env`` for
            # ``sync_provider_env`` below. When the credential prompt ran it already set
            # ``AZURE_OPENAI_BASE_URL`` in ``os.environ``, so this call short-circuits on the
            # configured endpoint instead of re-prompting — its only job now is to hand the
            # endpoint env back for the .env sync.
            azure_env = ensure_provider_endpoint_settings(provider)
            if azure_env is None:
                force_repick = True
                continue
            provider_extra_env = azure_env
            os.environ.update(azure_env)

        if provider.credential_kind == WizardCredentialKind.CLI:
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
    env_path = sync_provider_env(
        provider=provider,
        model=model,
        extra_env=provider_extra_env or None,
    )
    if credential_state == UNSAVED:
        # Re-apply the session-only value the user chose to continue with so the
        # in-process shell handoff can read it. A ``host`` value normally goes
        # straight to .env and never reaches this sink; it only lands here when
        # its .env write failed and the user picked "continue without saving".
        os.environ.update(session_env_sink)

    step_header(3, WIZARD_TOTAL_STEPS, "Integrations")
    try:
        configured_integrations, integration_env_path = (
            _integration_configurators_module._configure_selected_integrations()
        )
    except KeyboardInterrupt:
        cancelled = Text()
        cancelled.append(f"\n  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
        cancelled.append("Integration setup cancelled. AI config was kept.", style=TEXT)
        console.print(cancelled)
        configured_integrations = []
        integration_env_path = None

    summary_env_path = integration_env_path or str(env_path)
    _seed_onboarding_loops()

    step_header(4, WIZARD_TOTAL_STEPS, "Summary")
    render_saved_summary(
        provider_label=provider.label,
        model=model,
        saved_path=str(saved_path),
        env_path=summary_env_path,
        configured_integrations=configured_integrations,
        credential_line=_credential_line_for_saved_summary(
            provider, credential_state=credential_state
        ),
    )
    render_next_steps()
    return 0
