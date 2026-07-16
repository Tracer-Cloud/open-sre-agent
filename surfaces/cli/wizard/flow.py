"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import questionary
from rich.text import Text

import surfaces.cli.wizard._integration_configurators as _integration_configurators_module
from config.llm_auth.auth_method import (
    API_KEY_AUTH_METHOD,
    OAUTH_AUTH_METHOD,
    OAUTH_BACKEND_PROVIDER_BY_PROVIDER,
    LLMAuthMethod,
    normalize_llm_auth_method,
    supports_oauth_auth_method,
)
from config.llm_auth.records import save_provider_auth_record
from integrations.llm_cli.binary_resolver import diagnose_binary_path
from integrations.llm_cli.codex_oauth import CodexOAuthError, run_codex_oauth_login
from platform.terminal.theme import (
    ERROR,
    GLYPH_ERROR,
    GLYPH_WARNING,
    SECONDARY,
    TEXT,
    WARNING,
)
from surfaces.cli.wizard._ui import (
    Choice,
    WizardBack,
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
from surfaces.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption
from surfaces.cli.wizard.configurators.github import (
    DEFAULT_GITHUB_MCP_MODE,
    DEFAULT_GITHUB_MCP_URL,
)
from surfaces.cli.wizard.env_sync import sync_env_values, sync_provider_env
from surfaces.cli.wizard.integration_health import IntegrationHealthResult
from surfaces.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from surfaces.cli.wizard.store import get_store_path, save_local_config
from surfaces.cli.wizard.validation import build_demo_action_response as _build_demo_action_response

WIZARD_TOTAL_STEPS = 4
_CLI_SUBSCRIPTION_LOGIN_ARGS: dict[str, tuple[str, ...]] = {
    "claude-code": ("auth", "login"),
    "codex": ("login",),
}
_HIDDEN_ONBOARDING_BACKEND_PROVIDERS = frozenset(OAUTH_BACKEND_PROVIDER_BY_PROVIDER.values())
_CODEX_CONFIG_ERROR_RE = re.compile(
    r"Error loading configuration:\s*(?P<location>[^\n]+config\.toml:\d+:\d+):\s*(?P<detail>[^\n]+)"
)
_CODEX_CONFIG_LOCATION_RE = re.compile(r"^(?P<path>.+config\.toml):(?P<line>\d+):(?P<column>\d+)$")
_CODEX_STALE_SERVICE_TIER_DETAIL_RE = re.compile(
    r"unknown variant [`'\"]priority[`'\"], expected [`'\"]fast[`'\"] or [`'\"]flex[`'\"]"
)
_CODEX_PRIORITY_SERVICE_TIER_RE = re.compile(
    r"^(?P<prefix>[ \t]*service_tier[ \t]*=[ \t]*)(?P<quote>[\"'])"
    r"priority(?P=quote)(?P<suffix>[ \t]*(?:#.*)?)?(?P<newline>\r?\n)?$"
)

__all__ = [
    "DEFAULT_GITHUB_MCP_MODE",
    "DEFAULT_GITHUB_MCP_URL",
    "IntegrationHealthResult",
    "build_demo_action_response",
    "questionary",
]


# Re-export build_demo_action_response from validation as a stable module-level
# attribute. The wrapper indirection (instead of `from x import y`) is
# preserved so the function remains patchable via monkeypatch.setattr(flow,
# "build_demo_action_response", ...) — but we also keep the underlying import
# at module load time so the attribute exists immediately, even in CI parallel
# test workers where lazy imports inside the wrapper occasionally fail to
# materialize on first access.
def build_demo_action_response():
    return _build_demo_action_response()


def _provider_label_for_saved_summary(
    provider: ProviderOption, auth_method: str | None = None
) -> str:
    if normalize_llm_auth_method(auth_method) == OAUTH_AUTH_METHOD:
        return f"{_provider_choice_label(provider)} OAuth"
    return provider.label


def _credential_line_for_saved_summary(
    provider: ProviderOption, auth_method: str | None = None
) -> str:
    """One-line credential description for the post-wizard saved summary."""
    if normalize_llm_auth_method(auth_method) == OAUTH_AUTH_METHOD:
        if provider.value == "openai":
            return "OpenAI OAuth tokens (Codex CLI)"
        return f"{_provider_choice_label(provider)} OAuth session"
    if provider.credential_kind != "cli":
        return "system keychain"
    if provider.adapter_factory is None:
        return f"{provider.label} (CLI)"
    cli_adapter = provider.adapter_factory()
    return f"{provider.label} ({cli_adapter.auth_hint})"


@dataclass(frozen=True)
class _SubscriptionLoginResult:
    ok: bool
    detail: str = ""
    config_error: bool = False
    config_error_location: str = ""
    config_error_detail: str = ""


@dataclass(frozen=True)
class _LoginProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class _CodexConfigRepairResult:
    ok: bool
    detail: str


def _provider_choice_label(provider: ProviderOption) -> str:
    if provider.value == "openai":
        return "OpenAI"
    if provider.value == "anthropic":
        return "Anthropic"
    return provider.label


def _onboarding_provider_options() -> tuple[ProviderOption, ...]:
    return tuple(
        provider
        for provider in SUPPORTED_PROVIDERS
        if provider.value not in _HIDDEN_ONBOARDING_BACKEND_PROVIDERS
    )


def _auth_method_label(auth_method: str) -> str:
    return "OAuth" if normalize_llm_auth_method(auth_method) == OAUTH_AUTH_METHOD else "API key"


def _choose_auth_method(
    provider: ProviderOption,
    *,
    default: str | None,
) -> LLMAuthMethod:
    if provider.value in _HIDDEN_ONBOARDING_BACKEND_PROVIDERS:
        return OAUTH_AUTH_METHOD
    if not supports_oauth_auth_method(provider.value):
        return API_KEY_AUTH_METHOD
    method = _choose(
        f"Choose {provider.label.removesuffix(' API key')} auth method",
        [
            Choice(
                value=OAUTH_AUTH_METHOD,
                label="OAuth",
                hint="Browser login managed by onboarding",
            ),
            Choice(
                value=API_KEY_AUTH_METHOD,
                label="API key",
                hint=f"Paste {provider.api_key_env}",
            ),
        ],
        default=default
        if default in {API_KEY_AUTH_METHOD, OAUTH_AUTH_METHOD}
        else OAUTH_AUTH_METHOD,
        back_on_cancel=True,
    )
    return normalize_llm_auth_method(method)


def _oauth_backend_provider(provider: ProviderOption, auth_method: str) -> ProviderOption:
    if normalize_llm_auth_method(auth_method) != OAUTH_AUTH_METHOD:
        return provider
    backend = OAUTH_BACKEND_PROVIDER_BY_PROVIDER.get(provider.value)
    if backend is None:
        return provider
    return PROVIDER_BY_VALUE[backend]


def _persisted_auth_method(
    provider: ProviderOption, auth_method: str | None
) -> LLMAuthMethod | None:
    if auth_method is None:
        return None
    if provider.value in _HIDDEN_ONBOARDING_BACKEND_PROVIDERS or supports_oauth_auth_method(
        provider.value
    ):
        return normalize_llm_auth_method(auth_method)
    return None


def _credential_prompt_label(provider: ProviderOption) -> str:
    """Provider label without the credential kind when the choice already includes it."""
    suffix = f" {provider.credential_label}"
    if provider.label.lower().endswith(suffix.lower()):
        return provider.label[: -len(suffix)]
    return provider.label


def _persist_llm_credential(provider: ProviderOption, value: str) -> bool:
    """Persist one prompted credential where the runtime will actually read it.

    ``credential_kind == "host"`` values (e.g. the Ollama host URL) are plain
    runtime configuration, not secrets: the runtime resolves them from the
    environment only, never the keyring, so they belong in the project ``.env``.
    Everything else keeps the keyring path.
    """
    if provider.credential_kind == "host":
        sync_env_values({provider.api_key_env: value})
        os.environ[provider.api_key_env] = value
        return True
    return _persist_llm_api_key(provider.api_key_env, value)


def _azure_openai_endpoint_env(provider: ProviderOption) -> dict[str, str]:
    """Return Azure endpoint env vars, using the default API version when unset."""
    from core.llm.providers.azure_openai import resolve_azure_openai_api_version

    return {
        provider.endpoint_env: os.getenv(provider.endpoint_env, "").strip(),
        provider.api_version_env: resolve_azure_openai_api_version(),
    }


def _prompt_azure_openai_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Collect Azure OpenAI resource URL during onboarding."""
    from core.llm.providers.azure_openai import (
        normalize_azure_openai_base_url,
        resolve_azure_openai_api_version,
    )

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


def _ensure_azure_openai_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Return Azure endpoint env vars, prompting when missing."""
    from core.llm.providers.azure_openai import azure_openai_endpoint_configured

    if provider.value != "azure-openai":
        return {}
    if azure_openai_endpoint_configured():
        return _azure_openai_endpoint_env(provider)
    return _prompt_azure_openai_endpoint_settings(provider)


def _subscription_login_command(
    provider: ProviderOption, binary_path: str | None
) -> list[str] | None:
    """Return the vendor CLI login command for subscription-backed LLM providers."""
    if not binary_path:
        return None
    args = _CLI_SUBSCRIPTION_LOGIN_ARGS.get(provider.value)
    if args is None:
        return None
    return [binary_path, *args]


def _subscription_login_preflight_command(
    provider: ProviderOption, binary_path: str | None
) -> list[str] | None:
    """Return a non-OAuth command that validates config before interactive login."""
    if not binary_path:
        return None
    if provider.value == "codex":
        return [binary_path, "login", "--help"]
    return None


def _parse_codex_config_error_location(location: str) -> tuple[Path, int] | None:
    match = _CODEX_CONFIG_LOCATION_RE.match(location.strip())
    if match is None:
        return None
    try:
        line_no = int(match.group("line"))
    except ValueError:
        return None
    if line_no < 1:
        return None
    return Path(match.group("path")).expanduser(), line_no


def _codex_priority_service_tier_repair_hint(*, location: str, detail: str) -> str | None:
    if not _CODEX_STALE_SERVICE_TIER_DETAIL_RE.search(detail):
        return None
    parsed = _parse_codex_config_error_location(location)
    if parsed is None:
        return None
    path, line_no = parsed
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return None
    if line_no > len(lines):
        return None
    if _CODEX_PRIORITY_SERVICE_TIER_RE.match(lines[line_no - 1]) is None:
        return None
    return f"Change {path}:{line_no} service_tier from priority to fast"


def _repair_codex_priority_service_tier(*, location: str, detail: str) -> _CodexConfigRepairResult:
    hint = _codex_priority_service_tier_repair_hint(location=location, detail=detail)
    if hint is None:
        return _CodexConfigRepairResult(
            ok=False,
            detail="This Codex config error is not one OpenSRE can repair safely.",
        )
    parsed = _parse_codex_config_error_location(location)
    if parsed is None:
        return _CodexConfigRepairResult(ok=False, detail=f"Could not parse {location}.")

    path, line_no = parsed
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        return _CodexConfigRepairResult(
            ok=False,
            detail=f"Could not read {path}: {exc}",
        )
    if line_no > len(lines):
        return _CodexConfigRepairResult(
            ok=False,
            detail=f"Could not repair {path}: line {line_no} is outside the file.",
        )

    match = _CODEX_PRIORITY_SERVICE_TIER_RE.match(lines[line_no - 1])
    if match is None:
        return _CodexConfigRepairResult(
            ok=False,
            detail=f'Could not repair {path}: line {line_no} is no longer service_tier = "priority".',
        )

    lines[line_no - 1] = (
        f"{match.group('prefix')}{match.group('quote')}fast{match.group('quote')}"
        f"{match.group('suffix') or ''}{match.group('newline') or ''}"
    )
    try:
        path.write_text("".join(lines), encoding="utf-8")
    except OSError as exc:
        return _CodexConfigRepairResult(
            ok=False,
            detail=f"Could not update {path}: {exc}",
        )
    return _CodexConfigRepairResult(ok=True, detail=hint)


def _run_login_preflight_process(command: list[str]) -> _LoginProcessResult:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return _LoginProcessResult(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


def _run_interactive_login_process(command: list[str]) -> _LoginProcessResult:
    result = subprocess.run(command, check=False)
    return _LoginProcessResult(
        returncode=result.returncode,
    )


def _subscription_login_error(
    provider: ProviderOption, result: _LoginProcessResult
) -> _SubscriptionLoginResult:
    text = "\n".join(
        part.strip()
        for part in (getattr(result, "stdout", "") or "", getattr(result, "stderr", "") or "")
        if part and part.strip()
    )
    if provider.value == "codex":
        match = _CODEX_CONFIG_ERROR_RE.search(text)
        if match:
            location = match.group("location")
            detail = match.group("detail")
            return _SubscriptionLoginResult(
                ok=False,
                config_error=True,
                config_error_location=location,
                config_error_detail=detail,
                detail=(
                    "Codex CLI could not start because its local config is invalid: "
                    f"{location} ({detail}). "
                    "Fix that file, then retry OAuth login."
                ),
            )
    tail = text[:500]
    return _SubscriptionLoginResult(
        ok=False,
        detail=(
            f"Login exited with code {result.returncode}: {tail}"
            if tail
            else f"Login exited with code {result.returncode}."
        ),
    )


def _run_subscription_login(
    provider: ProviderOption, binary_path: str | None
) -> _SubscriptionLoginResult:
    """Launch the provider CLI login flow and report whether it exited cleanly."""
    if provider.value == "codex":
        _console.print(
            f"[{SECONDARY}]Starting OpenSRE Codex OAuth server on http://localhost:1455[/]"
        )
        try:
            oauth_result = run_codex_oauth_login()
        except CodexOAuthError as exc:
            detail = str(exc)
            _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {detail}[/]")
            return _SubscriptionLoginResult(ok=False, detail=detail)
        save_provider_auth_record(
            provider="codex",
            auth_name="chatgpt",
            kind="cli_subscription",
            source="codex-oauth",
            detail=oauth_result.detail,
        )
        _console.print(f"[{SECONDARY}]{oauth_result.detail}[/]")
        return _SubscriptionLoginResult(ok=True, detail=oauth_result.detail)

    command = _subscription_login_command(provider, binary_path)
    if command is None:
        auth_hint = provider.adapter_factory().auth_hint if provider.adapter_factory else ""
        detail = f"No browser login command is registered for {provider.label}. {auth_hint}"
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {detail}[/]")
        return _SubscriptionLoginResult(ok=False, detail=detail)

    preflight_command = _subscription_login_preflight_command(provider, binary_path)
    if preflight_command is not None:
        try:
            preflight_result = _run_login_preflight_process(preflight_command)
        except OSError as exc:
            detail = f"Could not check login config: {exc}"
            _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {detail}[/]")
            return _SubscriptionLoginResult(ok=False, detail=detail)
        if preflight_result.returncode != 0:
            login_result = _subscription_login_error(provider, preflight_result)
            _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {login_result.detail}[/]")
            return login_result

    _console.print(f"[{SECONDARY}]Launching {shlex.join(command)} for browser login…[/]")
    try:
        result = _run_interactive_login_process(command)
    except KeyboardInterrupt:
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  Login cancelled.[/]")
        return _SubscriptionLoginResult(ok=False, detail="Login cancelled.")
    except OSError as exc:
        detail = f"Could not launch login: {exc}"
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {detail}[/]")
        return _SubscriptionLoginResult(ok=False, detail=detail)
    if result.returncode != 0:
        login_result = _subscription_login_error(provider, result)
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {login_result.detail}[/]")
        return login_result
    return _SubscriptionLoginResult(ok=True)


def _recover_subscription_config_error(
    provider: ProviderOption,
    *,
    provider_label: str,
    binary_path: str | None,
    login_result: _SubscriptionLoginResult,
) -> Literal["ok", "continue", "repick"]:
    repair_hint: str | None = None
    if provider.value == "codex":
        repair_hint = _codex_priority_service_tier_repair_hint(
            location=login_result.config_error_location,
            detail=login_result.config_error_detail,
        )

    choices: list[Choice] = []
    if repair_hint is not None:
        choices.append(
            Choice(
                value="repair",
                label="Apply known Codex config fix and retry",
                hint=repair_hint,
            )
        )
    choices.extend(
        [
            Choice(
                value="retry",
                label="Retry after fixing local config",
                hint=login_result.detail,
            ),
            Choice(
                value="repick",
                label="Pick a different LLM provider",
                hint=None,
            ),
        ]
    )
    recovery = _choose(
        f"{provider_label} OAuth could not start. What next?",
        choices,
        default="repair" if repair_hint is not None else "retry",
    )
    if recovery == "repick":
        return "repick"
    if recovery != "repair":
        return "continue"

    repair_result = _repair_codex_priority_service_tier(
        location=login_result.config_error_location,
        detail=login_result.config_error_detail,
    )
    if not repair_result.ok:
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {repair_result.detail}[/]")
        return "continue"

    _console.print(f"[{SECONDARY}]  Updated Codex config: {repair_result.detail}.[/]")
    retry_result = _run_subscription_login(provider, binary_path)
    if retry_result.ok:
        return "ok"
    return "continue"


def _run_cli_llm_onboarding(
    provider: ProviderOption, *, display_label: str | None = None
) -> Literal["ok", "abort", "repick"]:
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
    provider_label = display_label or provider.label
    for _attempt in range(10):
        probe = adapter.detect()
        if probe.installed and probe.logged_in is True:
            _console.print(f"[{SECONDARY}]{probe.detail}[/]")
            return "ok"
        if probe.installed and probe.logged_in is not True:
            _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
            status_prompt = (
                f"{provider_label} requires login. What next?"
                if probe.logged_in is False
                else f"Could not verify {provider_label} login. What next?"
            )
            choices = []
            if _subscription_login_command(provider, probe.bin_path) is not None:
                choices.append(
                    Choice(
                        value="login",
                        label="Open browser login now",
                        hint=auth_hint,
                    )
                )
            choices.extend(
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
                ]
            )
            action = _choose(
                status_prompt,
                choices,
                default="login" if choices and choices[0].value == "login" else "retry",
            )
            if action == "repick":
                return "repick"
            if action == "login":
                login_result = _run_subscription_login(provider, probe.bin_path)
                if login_result.ok:
                    return "ok"
                if login_result.config_error:
                    recovery = _recover_subscription_config_error(
                        provider,
                        provider_label=provider_label,
                        binary_path=probe.bin_path,
                        login_result=login_result,
                    )
                    if recovery == "ok":
                        return "ok"
                    if recovery == "repick":
                        return "repick"
                continue
            continue
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
        action = _choose(
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
    raw_saved_auth_method = defaults.get("auth_method")
    saved_auth_method = (
        normalize_llm_auth_method(raw_saved_auth_method)
        if isinstance(raw_saved_auth_method, str)
        else API_KEY_AUTH_METHOD
    )
    provider_options = _onboarding_provider_options()
    provider_option_values = {p.value for p in provider_options}
    default_provider_value = (
        saved_provider_value
        if saved_provider_value in provider_option_values
        else provider_options[0].value
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
    model_provider: ProviderOption
    auth_method: LLMAuthMethod | None
    model: str
    provider_extra_env: dict[str, str] = {}
    while True:
        _step_header(2, WIZARD_TOTAL_STEPS, "LLM Provider")
        saved_provider = (
            PROVIDER_BY_VALUE.get(saved_provider_value) if saved_provider_value else None
        )
        if saved_provider is not None and not force_repick:
            saved_model_provider = _oauth_backend_provider(saved_provider, saved_auth_method)
            current_model = saved_model_value or saved_model_provider.default_model
            auth_segment = (
                f"  ·  {_auth_method_label(saved_auth_method)}"
                if supports_oauth_auth_method(saved_provider.value)
                else ""
            )
            _console.print(
                f"[{SECONDARY}]current provider  {_provider_choice_label(saved_provider)}{auth_segment}  ·  {current_model}[/]"
            )
            change_provider = _confirm("Change provider?", default=False)
        else:
            change_provider = True
        force_repick = False

        if change_provider:
            try:
                provider = PROVIDER_BY_VALUE[
                    _choose(
                        "Choose your LLM provider",
                        [
                            Choice(
                                value=p.value,
                                label=_provider_choice_label(p),
                                hint=p.group,
                            )
                            for p in provider_options
                        ],
                        default=default_provider_value,
                    )
                ]
                auth_method = _choose_auth_method(provider, default=OAUTH_AUTH_METHOD)
                model_provider = _oauth_backend_provider(provider, auth_method)
            except WizardBack:
                force_repick = True
                continue
            model = model_provider.default_model
            if auth_method == API_KEY_AUTH_METHOD and provider.credential_kind not in (
                "cli",
                "none",
            ):
                _step(provider.credential_label.title())
                try:
                    api_key = _prompt_value(
                        f"{_credential_prompt_label(provider)} {provider.credential_label} ({provider.api_key_env})",
                        default=provider.credential_default,
                        secret=provider.credential_secret,
                        back_on_cancel=True,
                    )
                except WizardBack:
                    force_repick = True
                    continue
                except KeyboardInterrupt:
                    _console.print(f"\n[{WARNING}]Setup cancelled.[/]")
                    return 1
                if not _persist_llm_credential(provider, api_key):
                    return 1
                azure_env = _ensure_azure_openai_endpoint_settings(provider)
                if azure_env is None:
                    force_repick = True
                    continue
                provider_extra_env = azure_env
                os.environ.update(azure_env)
        else:
            assert saved_provider is not None
            provider = saved_provider
            auth_method = saved_auth_method
            model_provider = _oauth_backend_provider(provider, auth_method)
            model = saved_model_value or model_provider.default_model
            if auth_method == API_KEY_AUTH_METHOD and provider.credential_kind not in (
                "cli",
                "none",
            ):
                has_api_key = bool(defaults["has_api_key"])
                legacy_api_key = str(defaults["legacy_api_key"] or "").strip()
                # A ``host`` credential (e.g. the Ollama host) is not a secret api key: never
                # migrate a stale legacy ``api_key`` value into it — that would leak a
                # secret-shaped value into .env and point the runtime at a bogus host. Fall
                # through to the host prompt instead (#3291).
                if not has_api_key and legacy_api_key and provider.credential_kind != "host":
                    if not _persist_llm_credential(provider, legacy_api_key):
                        return 1
                    has_api_key = True
                if not has_api_key:
                    _step(provider.credential_label.title())
                    try:
                        api_key = _prompt_value(
                            f"{_credential_prompt_label(provider)} {provider.credential_label} ({provider.api_key_env})",
                            default=provider.credential_default,
                            secret=provider.credential_secret,
                            back_on_cancel=True,
                        )
                    except WizardBack:
                        force_repick = True
                        continue
                    except KeyboardInterrupt:
                        _console.print(f"\n[{WARNING}]Setup cancelled.[/]")
                        return 1
                    if not _persist_llm_credential(provider, api_key):
                        return 1
            azure_env = _ensure_azure_openai_endpoint_settings(provider)
            if azure_env is None:
                force_repick = True
                continue
            provider_extra_env = azure_env
            os.environ.update(azure_env)

        if change_provider:
            try:
                model = _choose_model(
                    model_provider,
                    default=model,
                    prompt_label=(
                        f"{_provider_choice_label(provider)} OAuth"
                        if auth_method == OAUTH_AUTH_METHOD
                        else _provider_choice_label(provider)
                    ),
                    back_on_cancel=True,
                )
            except WizardBack:
                force_repick = True
                continue
        elif model_provider.models:
            current_display = model or "CLI default"
            _console.print(f"[{SECONDARY}]current model  {current_display}[/]")
            if _confirm("Change model?", default=False):
                model = _choose_model(
                    model_provider,
                    default=model,
                    prompt_label=(
                        f"{_provider_choice_label(provider)} OAuth"
                        if auth_method == OAUTH_AUTH_METHOD
                        else _provider_choice_label(provider)
                    ),
                )

        if model_provider.credential_kind == "cli":
            cli_out = _run_cli_llm_onboarding(
                model_provider,
                display_label=(
                    f"{_provider_choice_label(provider)} OAuth"
                    if auth_method == OAUTH_AUTH_METHOD
                    else None
                ),
            )
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
    persisted_auth_method = _persisted_auth_method(provider, auth_method)
    saved_path = save_local_config(
        wizard_mode=wizard_mode,
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env,
        model_env=model_provider.model_env,
        auth_method=persisted_auth_method,
        probes=probes,
    )
    env_path = sync_provider_env(
        provider=provider,
        model=model,
        model_provider=model_provider,
        auth_method=persisted_auth_method,
        extra_env=provider_extra_env or None,
    )

    _step_header(3, WIZARD_TOTAL_STEPS, "Integrations")
    try:
        configured_integrations, integration_env_path = (
            _integration_configurators_module._configure_selected_integrations()
        )
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
        provider_label=_provider_label_for_saved_summary(provider, persisted_auth_method),
        model=model,
        saved_path=str(saved_path),
        env_path=summary_env_path,
        configured_integrations=configured_integrations,
        credential_line=_credential_line_for_saved_summary(provider, persisted_auth_method),
    )
    _render_next_steps()
    return 0
