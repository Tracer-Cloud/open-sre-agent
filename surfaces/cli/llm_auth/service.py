"""Shared LLM auth setup operations for CLI, wizard, and REPL wrappers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from config.llm_auth.auth_method import OAUTH_AUTH_METHOD
from config.llm_auth.credentials import (
    delete as delete_provider_auth,
)
from config.llm_auth.credentials import (
    save_api_key,
)
from config.llm_auth.credentials import (
    status as provider_auth_status,
)
from config.llm_auth.credentials import (
    verify as verify_provider_auth,
)
from config.llm_auth.records import (
    delete_provider_auth_record,
    resolve_provider_auth_record,
    save_provider_auth_record,
)
from integrations.llm_cli.codex_oauth import CodexOAuthError, run_codex_oauth_login
from surfaces.cli.llm_auth.persist import AuthSetupError, persist_api_key_secret
from surfaces.cli.llm_auth.providers import (
    ProviderAuthProfile,
    provider_for_profile,
    resolve_auth_profile,
)
from surfaces.cli.wizard.config import PROVIDER_BY_VALUE, ProviderOption
from surfaces.cli.wizard.env_sync import sync_provider_env
from surfaces.cli.wizard.validation import validate_provider_credentials


@dataclass(frozen=True)
class AuthStatus:
    """Status row for one provider auth path."""

    provider: str
    label: str
    authenticated: bool
    source: str
    detail: str
    verified: bool = False
    stale: bool = False


@dataclass(frozen=True)
class AuthSetupResult:
    """Result from configuring a provider auth path."""

    provider: str
    model: str
    source: str
    detail: str
    env_path: Path | None


def _save_auth_record(
    *,
    provider: ProviderOption,
    profile: ProviderAuthProfile,
    source: str,
    detail: str,
) -> None:
    save_provider_auth_record(
        provider=provider.value,
        auth_name=profile.name,
        kind=profile.kind,
        source=source,
        detail=detail,
    )


def configure_api_key_provider(
    *,
    profile: ProviderAuthProfile,
    api_key: str,
    model: str | None = None,
    set_provider: bool = True,
    validate: bool = True,
    env_path: Path | None = None,
) -> AuthSetupResult:
    """Validate and persist an API-key provider credential."""
    provider = provider_for_profile(profile)
    if provider.credential_kind != "api_key" or not provider.api_key_env:
        raise AuthSetupError(f"{provider.label} does not use an OpenSRE-managed API key.")

    normalized_key = api_key.strip()
    if not normalized_key:
        raise AuthSetupError(f"{provider.api_key_env} cannot be empty.")

    selected_model = (model if model is not None else provider.default_model).strip()
    if validate:
        validation = validate_provider_credentials(
            provider=provider,
            api_key=normalized_key,
            model=selected_model,
        )
        if not validation.ok:
            raise AuthSetupError(validation.detail)

    try:
        save_api_key(
            provider.value,
            normalized_key,
            detail=f"{provider.api_key_env} stored in the system keychain.",
        )
    except (RuntimeError, ValueError) as exc:
        raise AuthSetupError(str(exc)) from exc

    written_path = (
        sync_provider_env(provider=provider, model=selected_model, env_path=env_path)
        if set_provider
        else None
    )
    detail = f"{provider.api_key_env} stored in the system keychain."
    _save_auth_record(provider=provider, profile=profile, source="keyring", detail=detail)
    return AuthSetupResult(
        provider=provider.value,
        model=selected_model,
        source="keyring",
        detail=detail,
        env_path=written_path,
    )


def _managed_codex_login_detail() -> str:
    try:
        result = run_codex_oauth_login()
    except CodexOAuthError as exc:
        raise AuthSetupError(str(exc)) from exc
    return result.detail


def _subscription_login_command(profile: ProviderAuthProfile, binary_path: str) -> list[str]:
    if profile.provider_value == "codex":
        return [binary_path, "login"]
    if profile.provider_value == "claude-code":
        return [binary_path, "auth", "login"]
    raise AuthSetupError(f"No interactive login command is registered for {profile.label}.")


def _run_vendor_login(profile: ProviderAuthProfile, binary_path: str) -> None:
    try:
        result = subprocess.run(_subscription_login_command(profile, binary_path), check=False)
    except OSError as exc:
        raise AuthSetupError(f"Could not launch {profile.label} login: {exc}") from exc
    if result.returncode != 0:
        raise AuthSetupError(f"{profile.label} login exited with code {result.returncode}.")


def configure_cli_subscription_provider(
    *,
    profile: ProviderAuthProfile,
    model: str | None = None,
    set_provider: bool = True,
    launch_login: bool = True,
    env_path: Path | None = None,
) -> AuthSetupResult:
    """Configure a CLI-backed subscription provider such as ChatGPT/Codex or Claude Code."""
    provider = provider_for_profile(profile)
    if provider.credential_kind != "cli" or provider.adapter_factory is None:
        raise AuthSetupError(f"{provider.label} is not a CLI-backed subscription provider.")

    adapter = provider.adapter_factory()
    probe = adapter.detect()
    if not probe.installed:
        raise AuthSetupError(f"{probe.detail} Install: {adapter.install_hint}")

    login_completed = False
    if probe.logged_in is not True:
        if profile.provider_value == "codex" and launch_login:
            detail = _managed_codex_login_detail()
            selected_model = (model if model is not None else provider.default_model).strip()
            public_provider = PROVIDER_BY_VALUE["openai"]
            written_path = (
                sync_provider_env(
                    provider=public_provider,
                    model=selected_model,
                    model_provider=provider,
                    auth_method=OAUTH_AUTH_METHOD,
                    env_path=env_path,
                )
                if set_provider
                else None
            )
            _save_auth_record(
                provider=provider,
                profile=profile,
                source="codex-oauth",
                detail=detail,
            )
            return AuthSetupResult(
                provider=provider.value,
                model=selected_model,
                source="codex-oauth",
                detail=detail,
                env_path=written_path,
            )
        if launch_login and probe.bin_path:
            _run_vendor_login(profile, probe.bin_path)
            login_completed = True
            probe = adapter.detect()
        if probe.logged_in is not True and not login_completed:
            raise AuthSetupError(f"{probe.detail} {adapter.auth_hint}")

    selected_model = (model if model is not None else provider.default_model).strip()
    written_path = (
        sync_provider_env(provider=provider, model=selected_model, env_path=env_path)
        if set_provider
        else None
    )
    detail = (
        f"{provider.label} login completed via {adapter.auth_hint.replace('Run: ', '')}."
        if login_completed and probe.logged_in is not True
        else probe.detail or f"{provider.label} is authenticated."
    )
    _save_auth_record(provider=provider, profile=profile, source="vendor-cli", detail=detail)
    return AuthSetupResult(
        provider=provider.value,
        model=selected_model,
        source="vendor-cli",
        detail=detail,
        env_path=written_path,
    )


def provider_status(raw_name: str) -> AuthStatus:
    """Return auth status for an auth profile or provider alias."""
    profile = resolve_auth_profile(raw_name)
    provider = provider_for_profile(profile)
    record = resolve_provider_auth_record(provider.value)

    if profile.kind == "api_key":
        resolved = provider_auth_status(provider.value)
        source = resolved.source
        authenticated = resolved.configured and not resolved.stale
        detail = resolved.detail
        if record.get("detail") and authenticated:
            detail = record["detail"]
        return AuthStatus(
            provider.value,
            profile.label,
            authenticated,
            source,
            detail,
            verified=resolved.verified,
            stale=resolved.stale,
        )

    record_verified = (record.get("verified") or "").strip().lower()
    record_stale = (record.get("stale") or "").strip().lower()
    if (
        record.get("source") == "codex-oauth"
        and record_verified != "false"
        and record_stale != "true"
    ):
        return AuthStatus(
            provider.value,
            profile.label,
            True,
            "codex-oauth",
            record.get("detail") or "OpenAI OAuth tokens are stored for Codex.",
            verified=True,
        )

    if provider.adapter_factory is None:
        return AuthStatus(provider.value, profile.label, False, "none", "No adapter registered.")
    probe = provider.adapter_factory().detect()
    authenticated = probe.installed and probe.logged_in is True
    cli_source = "vendor-cli" if authenticated else "none"
    detail = probe.detail
    if record.get("detail") and authenticated:
        detail = record["detail"]
    return AuthStatus(
        provider.value, profile.label, authenticated, cli_source, detail, verified=authenticated
    )


def verify_provider(raw_name: str) -> AuthStatus:
    """Intentionally resolve request-time credentials and refresh metadata."""
    profile = resolve_auth_profile(raw_name)
    provider = provider_for_profile(profile)
    if profile.kind != "api_key":
        return provider_status(raw_name)
    resolved = verify_provider_auth(provider.value)
    return AuthStatus(
        provider.value,
        profile.label,
        resolved.configured and not resolved.stale,
        resolved.source,
        resolved.detail,
        verified=resolved.verified,
        stale=resolved.stale,
    )


def logout_provider(raw_name: str, *, vendor: bool = False) -> str:
    """Clear OpenSRE-managed auth for a provider.

    For API-key providers this deletes the keyring API key. For subscription
    CLI providers, OpenSRE clears only its metadata unless ``vendor=True`` is
    requested; the actual session belongs to the vendor CLI.
    """
    profile = resolve_auth_profile(raw_name)
    provider = provider_for_profile(profile)
    delete_provider_auth_record(provider.value)

    if profile.kind == "api_key":
        delete_provider_auth(provider.value)
        return f"Removed {provider.api_key_env} from OpenSRE's keyring store."

    if not vendor:
        return (
            f"Cleared OpenSRE auth metadata for {profile.label}. "
            f"Vendor CLI session remains; logout with: {profile.auth_hint.replace('login', 'logout')}"
        )

    if provider.adapter_factory is None:
        raise AuthSetupError(f"No adapter is registered for {provider.label}.")
    probe = provider.adapter_factory().detect()
    if not probe.installed or not probe.bin_path:
        raise AuthSetupError(f"{provider.label} CLI is not installed.")
    command = profile.auth_hint.replace("Run: ", "").replace("login", "logout").split()
    try:
        result = subprocess.run([probe.bin_path, *command[1:]], check=False)
    except OSError as exc:
        raise AuthSetupError(f"Could not launch vendor logout: {exc}") from exc
    if result.returncode != 0:
        raise AuthSetupError(f"Vendor logout exited with code {result.returncode}.")
    return f"Logged out of {profile.label} via vendor CLI."


__all__ = [
    "AuthSetupError",
    "AuthSetupResult",
    "AuthStatus",
    "configure_api_key_provider",
    "configure_cli_subscription_provider",
    "logout_provider",
    "persist_api_key_secret",
    "provider_status",
    "verify_provider",
]
