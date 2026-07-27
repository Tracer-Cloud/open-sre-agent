"""Prompt-safe LLM auth status and request-scoped credential resolution."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    ProviderSpec,
    provider_spec,
    require_provider_spec,
)
from config.llm_auth.records import (
    delete_provider_auth_record,
    resolve_provider_auth_record,
    save_provider_auth_record,
    save_provider_auth_record_values,
)

CredentialSource = Literal[
    "env",
    "keyring",
    "metadata",
    "cli",
    "ambient",
    "local",
    "none",
    "unknown",
]


class MissingLLMCredentialError(RuntimeError):
    """Raised when a selected provider lacks request-time credentials."""


@dataclass(frozen=True)
class CredentialStatus:
    """Prompt-safe status for a provider auth path."""

    provider: str
    configured: bool
    source: CredentialSource
    verified: bool
    stale: bool
    detail: str


@dataclass(frozen=True)
class _AmbientProbeResult:
    """Outcome of an actual (not env-presence-only) ambient credential check."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class CredentialResolution:
    """Request-time credential resolution for one provider."""

    provider: str
    api_key: str
    source: CredentialSource
    detail: str

    @property
    def ok(self) -> bool:
        return bool(self.api_key) or self.source in {"cli", "ambient", "local"}

    def __repr__(self) -> str:
        redacted = "<set>" if self.api_key else "<empty>"
        return (
            "CredentialResolution("
            f"provider={self.provider!r}, api_key={redacted}, "
            f"source={self.source!r}, detail={self.detail!r})"
        )


def _bool_record_value(record: dict[str, str], key: str, default: bool) -> bool:
    raw = record.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_source(raw: str | None, *, fallback: CredentialSource) -> CredentialSource:
    value = (raw or "").strip().lower()
    allowed = {"env", "keyring", "metadata", "cli", "ambient", "local", "none", "unknown"}
    return value if value in allowed else fallback  # type: ignore[return-value]


def _env_value(env_var: str) -> str:
    return os.getenv(env_var, "").strip()


def _probe_vertex_ai_ambient(spec: ProviderSpec) -> _AmbientProbeResult:
    """Attempt real Google ADC resolution; env vars alone do not prove ADC works."""
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    project_hint = _env_value(spec.project_env) if spec.project_env else ""
    location = _env_value(spec.location_env) if spec.location_env else ""
    location_detail = f", location {location}" if location else ""

    try:
        _credentials, discovered_project = google.auth.default()
    except DefaultCredentialsError as exc:
        return _AmbientProbeResult(
            ok=False,
            detail=(
                f"Google Application Default Credentials are not configured ({exc}). "
                "Run `gcloud auth application-default login` or set "
                "GOOGLE_APPLICATION_CREDENTIALS."
            ),
        )
    except Exception as exc:
        return _AmbientProbeResult(
            ok=False,
            detail=f"Could not resolve Google Application Default Credentials: {exc}",
        )

    project = project_hint or discovered_project
    if not project:
        return _AmbientProbeResult(
            ok=False,
            detail=(
                "Google Application Default Credentials resolved, but no GCP project "
                "could be determined (ADC did not discover one and VERTEX_AI_PROJECT is "
                "not set). Set VERTEX_AI_PROJECT — Vertex AI requests require a project."
            ),
        )
    return _AmbientProbeResult(
        ok=True,
        detail=(
            f"Google Application Default Credentials resolved (project {project}"
            f"{location_detail}); {spec.label} uses ADC."
        ),
    )


def _probe_bedrock_ambient(spec: ProviderSpec) -> _AmbientProbeResult:
    region = os.getenv("AWS_REGION", "").strip() or os.getenv("AWS_DEFAULT_REGION", "").strip()
    if not region:
        return _AmbientProbeResult(detail="AWS_REGION or AWS_DEFAULT_REGION is not set.", ok=False)
    return _AmbientProbeResult(
        ok=True,
        detail=f"AWS region is configured ({region}); {spec.label} uses the AWS credential chain.",
    )


_AMBIENT_PROBES: dict[str, Callable[[ProviderSpec], _AmbientProbeResult]] = {
    "vertex-ai": _probe_vertex_ai_ambient,
    "bedrock": _probe_bedrock_ambient,
}


def _source_status(provider: str, source: CredentialSource, detail: str) -> CredentialStatus:
    return CredentialStatus(
        provider=provider,
        configured=source not in {"none", "unknown"},
        source=source,
        verified=source not in {"metadata", "unknown"},
        stale=False,
        detail=detail,
    )


def _record_status(spec: ProviderSpec, record: dict[str, str]) -> CredentialStatus:
    source = _normalize_source(record.get("source"), fallback="metadata")
    stale = _bool_record_value(record, "stale", False)
    verified = _bool_record_value(record, "verified", not stale)
    detail = record.get("detail") or (
        f"{spec.api_key_env} was previously saved; run `opensre auth verify {spec.value}` "
        "to confirm it is still available."
    )
    return CredentialStatus(
        provider=spec.value,
        configured=True,
        source=source if source != "keyring" else "metadata",
        verified=verified and not stale,
        stale=stale,
        detail=detail,
    )


def status(provider: str) -> CredentialStatus:
    """Return prompt-safe provider auth status.

    This function must not read Keychain secrets. It may inspect environment,
    non-secret metadata, CLI adapter probes, and ambient/local config markers.
    """
    spec = provider_spec(provider)
    if spec is None:
        return CredentialStatus(
            provider=provider.strip().lower(),
            configured=False,
            source="unknown",
            verified=False,
            stale=False,
            detail=f"Unsupported LLM provider: {provider}",
        )

    if spec.credential_kind == "api_key":
        if spec.api_key_env and _env_value(spec.api_key_env):
            return _source_status(
                spec.value,
                "env",
                f"{spec.api_key_env} is set in the environment.",
            )
        record = resolve_provider_auth_record(spec.value)
        if record:
            return _record_status(spec, record)
        return CredentialStatus(
            provider=spec.value,
            configured=False,
            source="none",
            verified=False,
            stale=False,
            detail=f"{spec.api_key_env} is not configured.",
        )

    if spec.credential_kind == "cli":
        record = resolve_provider_auth_record(spec.value)
        record_source = _normalize_source(record.get("source"), fallback="metadata")
        stale = _bool_record_value(record, "stale", False)
        verified = _bool_record_value(record, "verified", False)
        if record and verified and not stale:
            return CredentialStatus(
                provider=spec.value,
                configured=True,
                source="cli" if record_source in {"metadata", "unknown", "none"} else record_source,
                verified=True,
                stale=False,
                detail=record.get("detail") or f"{spec.label} auth metadata is present.",
            )
        try:
            from integrations.llm_cli.registry import get_cli_provider_registration

            reg = get_cli_provider_registration(spec.value)
            if reg is None:
                return CredentialStatus(
                    spec.value,
                    False,
                    "none",
                    False,
                    False,
                    "No CLI adapter registered.",
                )
            probe = reg.adapter_factory().detect()
        except Exception as exc:
            return CredentialStatus(
                spec.value,
                False,
                "unknown",
                False,
                False,
                f"CLI auth status could not be checked: {exc}",
            )
        configured = probe.installed and probe.logged_in is True
        source: CredentialSource = "cli" if configured else "none"
        return CredentialStatus(
            provider=spec.value,
            configured=configured,
            source=source,
            verified=configured,
            stale=False,
            detail=probe.detail,
        )

    if spec.credential_kind == "ambient":
        ambient_probe = _AMBIENT_PROBES.get(spec.value)
        if ambient_probe is None:
            return CredentialStatus(
                spec.value,
                False,
                "unknown",
                False,
                False,
                "No ambient credential probe registered.",
            )
        try:
            result = ambient_probe(spec)
        except Exception as exc:
            return CredentialStatus(
                spec.value,
                False,
                "unknown",
                False,
                False,
                f"{spec.label} ambient credential check failed unexpectedly: {exc}",
            )
        return CredentialStatus(
            provider=spec.value,
            configured=result.ok,
            source="ambient" if result.ok else "none",
            verified=result.ok,
            stale=False,
            detail=result.detail,
        )

    if spec.credential_kind == "local":
        host = os.getenv("OLLAMA_HOST", "").strip() or "http://localhost:11434"
        return CredentialStatus(
            provider=spec.value,
            configured=True,
            source="local",
            verified=True,
            stale=False,
            detail=f"Ollama host: {host}.",
        )

    return CredentialStatus(spec.value, False, "unknown", False, False, "Unknown auth kind.")


def _mark_stale(spec: ProviderSpec, detail: str) -> None:
    record = resolve_provider_auth_record(spec.value)
    if not record:
        return
    save_provider_auth_record_values(
        spec.value,
        {
            **record,
            "source": record.get("source") or "metadata",
            "detail": detail,
            "verified": "false",
            "stale": "true",
        },
    )


def resolve_for_request(provider: str) -> CredentialResolution:
    """Resolve request-time auth for exactly one selected provider."""
    spec = require_provider_spec(provider)
    if spec.credential_kind == "api_key":
        env_value = _env_value(spec.api_key_env)
        if env_value:
            return CredentialResolution(
                provider=spec.value,
                api_key=env_value,
                source="env",
                detail=f"{spec.api_key_env} resolved from environment.",
            )

        import keyring.errors

        from config.llm_keyring import keyring_is_disabled, read_keychain_secret

        if not keyring_is_disabled():
            try:
                key = read_keychain_secret(spec.api_key_env)
            except keyring.errors.KeyringError as exc:
                # The backend itself couldn't be reached (e.g. no D-Bus/Secret
                # Service session in this process) — this is not evidence the
                # credential is missing, so leave any previously-verified
                # metadata untouched instead of marking it stale.
                return CredentialResolution(
                    provider=spec.value,
                    api_key="",
                    source="unknown",
                    detail=(
                        f"Could not reach the system keychain to check {spec.api_key_env}: "
                        f"{exc}. Retry once the keychain is reachable."
                    ),
                )
            if key:
                save_provider_auth_record(
                    provider=spec.value,
                    auth_name=spec.value,
                    kind="api_key",
                    source="keyring",
                    detail=f"{spec.api_key_env} stored in the system keychain.",
                    verified=True,
                    stale=False,
                    env_var=spec.api_key_env,
                )
                return CredentialResolution(
                    provider=spec.value,
                    api_key=key,
                    source="keyring",
                    detail=f"{spec.api_key_env} resolved from secure local storage.",
                )

        detail = (
            f"Missing credential for LLM provider '{spec.value}'. Set {spec.api_key_env} "
            f"or run `opensre auth login {spec.value}`."
        )
        _mark_stale(spec, detail)
        return CredentialResolution(spec.value, "", "none", detail)

    if spec.credential_kind == "cli":
        return CredentialResolution(
            provider=spec.value,
            api_key="",
            source="cli",
            detail=f"{spec.label} uses vendor CLI authentication.",
        )
    if spec.credential_kind == "ambient":
        return CredentialResolution(
            provider=spec.value,
            api_key="",
            source="ambient",
            detail=f"{spec.label} uses ambient credentials.",
        )
    if spec.credential_kind == "local":
        return CredentialResolution(
            provider=spec.value,
            api_key="",
            source="local",
            detail=f"{spec.label} uses local runtime configuration.",
        )
    return CredentialResolution(spec.value, "", "unknown", "Unsupported provider auth kind.")


def require_for_request(provider: str) -> CredentialResolution:
    """Resolve request-time auth or raise an actionable error."""
    resolution = resolve_for_request(provider)
    if not resolution.ok:
        raise MissingLLMCredentialError(resolution.detail)
    return resolution


def resolve_api_key_env_for_request(env_var: str) -> str:
    """Resolve one API-key env var through the request-time provider boundary."""
    normalized = env_var.strip()
    for provider, provider_env in API_KEY_PROVIDER_ENVS.items():
        if provider_env == normalized:
            return resolve_for_request(provider).api_key
    from config.llm_credentials import resolve_env_credential

    return resolve_env_credential(normalized)


def save_api_key(provider: str, value: str, *, detail: str | None = None) -> None:
    """Store an OpenSRE-managed API key and refresh prompt-safe metadata."""
    spec = require_provider_spec(provider)
    if not spec.uses_open_sre_api_key:
        raise ValueError(f"{spec.value} does not use an OpenSRE-managed API key")
    from config.llm_keyring import save_keyring_secret

    save_keyring_secret(spec.api_key_env, value)
    save_provider_auth_record(
        provider=spec.value,
        auth_name=spec.value,
        kind="api_key",
        source="keyring",
        detail=detail or f"{spec.api_key_env} stored in the system keychain.",
        verified=True,
        stale=False,
        env_var=spec.api_key_env,
    )


def delete(provider: str) -> None:
    """Delete OpenSRE-managed provider auth metadata and API key when applicable."""
    spec = require_provider_spec(provider)
    if spec.uses_open_sre_api_key:
        from config.llm_keyring import delete_keyring_secret

        delete_keyring_secret(spec.api_key_env)
    delete_provider_auth_record(spec.value)


def verify(provider: str) -> CredentialStatus:
    """Intentionally check request-time credentials and update metadata."""
    resolution = resolve_for_request(provider)
    if resolution.ok:
        return CredentialStatus(
            provider=resolution.provider,
            configured=True,
            source=resolution.source,
            verified=True,
            stale=False,
            detail=resolution.detail,
        )
    return status(provider)


def source_for_api_key_env(env_var: str) -> CredentialSource:
    """Prompt-safe source lookup for legacy env-var-based callers."""
    normalized = env_var.strip()
    if _env_value(normalized):
        return "env"
    for provider, provider_env in API_KEY_PROVIDER_ENVS.items():
        if provider_env == normalized:
            provider_status = status(provider)
            return provider_status.source if provider_status.configured else "none"
    return "none"


def has_api_key_env_status(env_var: str) -> bool:
    """Prompt-safe availability check for legacy env-var-based callers."""
    return source_for_api_key_env(env_var) != "none"


def llm_api_key_source(env_var: str) -> str:
    """Return where an LLM credential resolves from: ``env``, ``keyring``, or ``none``."""
    import keyring
    import keyring.errors

    from config.llm_keyring import (
        _KEYRING_SERVICE,
        keyring_is_disabled,
        macos_keychain_item_exists,
    )

    prompt_safe_source = source_for_api_key_env(env_var)
    if prompt_safe_source != "none":
        return prompt_safe_source
    if env_var.strip() in set(API_KEY_PROVIDER_ENVS.values()):
        return "none"
    if os.getenv(env_var, "").strip():
        return "env"
    if keyring_is_disabled():
        return "none"
    item_exists = macos_keychain_item_exists(env_var)
    if item_exists is not None:
        return "keyring" if item_exists else "none"
    try:
        if (keyring.get_password(_KEYRING_SERVICE, env_var) or "").strip():
            return "keyring"
    except keyring.errors.KeyringError:
        return "none"
    return "none"


def has_llm_api_key(env_var: str) -> bool:
    """Return True when an API key is available from env or secure local storage."""
    from config.llm_keyring import (
        keyring_is_disabled,
        macos_keychain_item_exists,
        resolve_keyring_secret,
    )

    if has_api_key_env_status(env_var):
        return True
    if env_var.strip() in set(API_KEY_PROVIDER_ENVS.values()):
        return False
    if os.getenv(env_var, "").strip():
        return True
    if keyring_is_disabled():
        return False
    item_exists = macos_keychain_item_exists(env_var)
    if item_exists is not None:
        return item_exists
    return bool(resolve_keyring_secret(env_var))


__all__ = [
    "CredentialResolution",
    "CredentialSource",
    "CredentialStatus",
    "MissingLLMCredentialError",
    "delete",
    "has_api_key_env_status",
    "has_llm_api_key",
    "llm_api_key_source",
    "require_for_request",
    "resolve_api_key_env_for_request",
    "resolve_for_request",
    "save_api_key",
    "source_for_api_key_env",
    "status",
    "verify",
]
