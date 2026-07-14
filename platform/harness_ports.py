"""Agent-harness ports — integrations, tools, and repository scope without tier violations.

Adapters register at startup via :func:`surfaces.interactive_shell.ui.output.boundary.install_harness_ports`
(shell/tests) or the gateway boot path in :mod:`gateway.runtime.manager` (duplicate wiring).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.strict_config import StrictConfigModel
from core.tool_framework.registered_tool import RegisteredTool

if TYPE_CHECKING:
    from core.agent_harness.ports import ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Integration resolution
# ---------------------------------------------------------------------------

RemoteIntegrationsFetcher = Callable[[str, str], list[dict[str, Any]]]
LoadIntegrationsFn = Callable[[], list[dict[str, Any]]]
IntegrationStorePathFn = Callable[[], str]
LoadEnvIntegrationsFn = Callable[[], list[dict[str, Any]]]
ClassifyIntegrationsFn = Callable[[list[dict[str, Any]]], dict[str, Any]]
MergeLocalIntegrationsFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]
]
MergeIntegrationsByServiceFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    list[dict[str, Any]],
]
ConfiguredIntegrationServicesFn = Callable[[], tuple[str, ...]]


def _default_fetch_remote(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    _ = (org_id, auth_token)
    return []


def _default_load_integrations() -> list[dict[str, Any]]:
    return []


def _default_store_path() -> str:
    return ""


def _default_load_env_integrations() -> list[dict[str, Any]]:
    return []


def _default_classify_integrations(_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {}


def _default_merge_local(
    store: list[dict[str, Any]], env: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [*store, *env]


def _default_merge_by_service(
    env: list[dict[str, Any]],
    store: list[dict[str, Any]],
    remote: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [*env, *store, *remote]


def _default_configured_services() -> tuple[str, ...]:
    return ()


_fetch_remote: RemoteIntegrationsFetcher = _default_fetch_remote
_load_integrations: LoadIntegrationsFn = _default_load_integrations
_store_path: IntegrationStorePathFn = _default_store_path
_load_env_integrations: LoadEnvIntegrationsFn = _default_load_env_integrations
_classify_integrations: ClassifyIntegrationsFn = _default_classify_integrations
_merge_local_integrations: MergeLocalIntegrationsFn = _default_merge_local
_merge_integrations_by_service: MergeIntegrationsByServiceFn = _default_merge_by_service
_configured_integration_services: ConfiguredIntegrationServicesFn = _default_configured_services


def set_remote_integrations_fetcher(fetcher: RemoteIntegrationsFetcher) -> None:
    global _fetch_remote
    _fetch_remote = fetcher


def fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
    return _fetch_remote(org_id, auth_token)


def configured_integration_services() -> tuple[str, ...]:
    return _configured_integration_services()


def set_integration_resolution_adapters(
    *,
    load_integrations: LoadIntegrationsFn | None = None,
    integration_store_path: IntegrationStorePathFn | None = None,
    load_env_integrations: LoadEnvIntegrationsFn | None = None,
    classify_integrations: ClassifyIntegrationsFn | None = None,
    merge_local_integrations: MergeLocalIntegrationsFn | None = None,
    merge_integrations_by_service: MergeIntegrationsByServiceFn | None = None,
    configured_services: ConfiguredIntegrationServicesFn | None = None,
) -> None:
    global _load_integrations, _store_path, _load_env_integrations
    global _classify_integrations, _merge_local_integrations
    global _merge_integrations_by_service, _configured_integration_services
    if load_integrations is not None:
        _load_integrations = load_integrations
    if integration_store_path is not None:
        _store_path = integration_store_path
    if load_env_integrations is not None:
        _load_env_integrations = load_env_integrations
    if classify_integrations is not None:
        _classify_integrations = classify_integrations
    if merge_local_integrations is not None:
        _merge_local_integrations = merge_local_integrations
    if merge_integrations_by_service is not None:
        _merge_integrations_by_service = merge_integrations_by_service
    if configured_services is not None:
        _configured_integration_services = configured_services


class IntegrationResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    resolved_integrations: dict[str, Any] | None = None
    auth_token: str = Field(default="", alias="_auth_token")
    org_id: str = ""

    @field_validator("auth_token", "org_id", mode="before")
    @classmethod
    def _coerce_optional_string(cls, value: Any) -> str:
        return str(value or "").strip()


class IntegrationResolutionResult(StrictConfigModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_integrations: dict[str, Any] = Field(default_factory=dict)
    progress_message: str | None = None

    @property
    def services(self) -> tuple[str, ...]:
        return tuple(
            service for service in self.resolved_integrations if not service.startswith("_")
        )


def resolve_integrations(state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return resolve_integrations_with_metadata(state).resolved_integrations


def resolve_integrations_with_metadata(
    state: Mapping[str, Any] | None = None,
) -> IntegrationResolutionResult:
    request = IntegrationResolutionRequest.model_validate(state or {})
    existing = request.resolved_integrations
    if existing:
        return IntegrationResolutionResult(resolved_integrations=dict(existing))

    org_id = request.org_id
    auth_token = _strip_bearer(request.auth_token)

    if auth_token:
        if not org_id:
            org_id = _decode_org_id_from_token(auth_token)
        if not org_id:
            logger.warning("_auth_token present but could not decode org_id")
            return IntegrationResolutionResult()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=auth_token)
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            return IntegrationResolutionResult()
        resolved = _classify_integrations(all_integrations)
        return IntegrationResolutionResult(
            resolved_integrations=resolved,
            progress_message=_resolved_message(resolved),
        )

    env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
    if env_token:
        if not org_id:
            org_id = _decode_org_id_from_token(env_token)
        if not org_id:
            return _resolve_from_local_sources()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=env_token)
        except Exception:
            logger.debug(
                "Remote integrations fetch failed for org %s, falling back to local",
                org_id,
                exc_info=True,
            )
            return _resolve_from_local_sources()
        return _resolve_remote_with_local_fallback(all_integrations)

    return _resolve_from_local_sources()


def _resolved_message(resolved: dict[str, Any]) -> str:
    services = [service for service in resolved if not service.startswith("_")]
    return f"Resolved integrations: {services}" if services else "No active integrations found"


def _resolve_from_local_sources() -> IntegrationResolutionResult:
    store_integrations = _load_integrations()
    env_integrations = _load_env_integrations() if not store_integrations else []
    integrations = _merge_local_integrations(store_integrations, env_integrations)
    if not integrations:
        return IntegrationResolutionResult(
            resolved_integrations={},
            progress_message=(
                f"No auth context and no local integrations found "
                f"(store: {_store_path()}, env fallback checked)"
            ),
        )

    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]
    source_labels: list[str] = []
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")
    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved local integrations from {', '.join(source_labels)}: {services}"
            if source_labels
            else f"Resolved local integrations: {services}"
        ),
    )


def _resolve_remote_with_local_fallback(
    remote_integrations: list[dict[str, Any]],
) -> IntegrationResolutionResult:
    store_integrations = _load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        env_integrations,
        store_integrations,
        remote_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]

    source_labels = ["remote"]
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")

    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved integrations from {', '.join(source_labels)}: {services}"
            if services
            else "No active integrations found"
        ),
    )


def _decode_org_id_from_token(token: str) -> str:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("organization") or claims.get("org_id") or ""
    except Exception:
        logger.debug("Failed to decode org_id from JWT token", exc_info=True)
        return ""


def _strip_bearer(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token.split(None, 1)[1].strip()
    return token


# ---------------------------------------------------------------------------
# Tool registry + investigation tools
# ---------------------------------------------------------------------------

InvestigationToolsFn = Callable[[dict[str, Any]], list[RegisteredTool]]


class _EmptyToolRegistry:
    """Default tool registry that resolves nothing until one is injected."""

    def tools_for_surface(self, _surface: str) -> list[RegisteredTool]:
        return []

    def tool_map_for_surface(self, _surface: str) -> dict[str, RegisteredTool]:
        return {}


def _default_investigation_tools(_resolved: dict[str, Any]) -> list[RegisteredTool]:
    return []


_tool_registry: ToolRegistry = _EmptyToolRegistry()
_get_investigation_tools: InvestigationToolsFn = _default_investigation_tools


def get_surface_tools(surface: str) -> list[RegisteredTool]:
    return _tool_registry.tools_for_surface(surface)


def get_surface_tool_map(surface: str) -> dict[str, RegisteredTool]:
    return _tool_registry.tool_map_for_surface(surface)


def get_investigation_tools(resolved_integrations: dict[str, Any]) -> list[RegisteredTool]:
    return _get_investigation_tools(resolved_integrations)


def set_tool_registry(registry: ToolRegistry) -> None:
    global _tool_registry
    _tool_registry = registry


def set_investigation_tools_adapter(
    get_investigation_tools: InvestigationToolsFn | None = None,
) -> None:
    global _get_investigation_tools
    if get_investigation_tools is not None:
        _get_investigation_tools = get_investigation_tools


# ---------------------------------------------------------------------------
# CLI-backed LLM (integrations.llm_cli)
# ---------------------------------------------------------------------------

CliProviderRegistrationFn = Callable[[str], Any]
BuildCliClientFn = Callable[..., Any]
FlattenCliMessagesFn = Callable[[list[dict[str, Any]]], str]


def _default_cli_provider_registration(_provider: str) -> Any:
    return None


def _cli_llm_backend_unavailable(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(
        "CLI LLM backend is not registered — call install_harness_ports() at startup."
    )


_cli_provider_registration_fn: CliProviderRegistrationFn = _default_cli_provider_registration
_build_cli_client_fn: BuildCliClientFn = _cli_llm_backend_unavailable
_flatten_cli_messages_fn: FlattenCliMessagesFn = _cli_llm_backend_unavailable


def cli_provider_registration(provider: str) -> Any:
    return _cli_provider_registration_fn(provider)


def build_cli_client(
    adapter: Any,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    model_type: Any = None,
) -> Any:
    return _build_cli_client_fn(adapter, model=model, max_tokens=max_tokens, model_type=model_type)


def flatten_cli_messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    return _flatten_cli_messages_fn(messages)


def set_cli_llm_adapters(
    *,
    cli_provider_registration: CliProviderRegistrationFn | None = None,
    build_cli_client: BuildCliClientFn | None = None,
    flatten_cli_messages: FlattenCliMessagesFn | None = None,
) -> None:
    global _cli_provider_registration_fn, _build_cli_client_fn, _flatten_cli_messages_fn
    if cli_provider_registration is not None:
        _cli_provider_registration_fn = cli_provider_registration
    if build_cli_client is not None:
        _build_cli_client_fn = build_cli_client
    if flatten_cli_messages is not None:
        _flatten_cli_messages_fn = flatten_cli_messages


# ---------------------------------------------------------------------------
# GitHub repo scope
# ---------------------------------------------------------------------------

InferGithubRepoScopeFn = Callable[
    [
        str,
        Sequence[tuple[str, str]] | None,
        Mapping[str, str] | None,
        str | Path | None,
        tuple[str, str] | None,
    ],
    tuple[str, str] | None,
]
ApplyGithubRepoScopeFn = Callable[[dict[str, Any], str, str], dict[str, Any]]


def _default_infer_github_scope(
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
    cached: tuple[str, str] | None,
) -> tuple[str, str] | None:
    _ = (message, conversation_messages, env, cwd, cached)
    return None


def _default_apply_github_scope(resolved: dict[str, Any], owner: str, repo: str) -> dict[str, Any]:
    _ = (owner, repo)
    return dict(resolved)


_infer_github_repo_scope: InferGithubRepoScopeFn = _default_infer_github_scope
_apply_github_repo_scope: ApplyGithubRepoScopeFn = _default_apply_github_scope


def infer_github_repo_scope(
    *,
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    cached: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    return _infer_github_repo_scope(message, conversation_messages, env, cwd, cached)


def apply_github_repo_scope(
    resolved: dict[str, Any],
    owner: str,
    repo: str,
) -> dict[str, Any]:
    return _apply_github_repo_scope(resolved, owner, repo)


def set_github_repo_scope_adapters(
    *,
    infer_scope: InferGithubRepoScopeFn | None = None,
    apply_scope: ApplyGithubRepoScopeFn | None = None,
) -> None:
    global _infer_github_repo_scope, _apply_github_repo_scope
    if infer_scope is not None:
        _infer_github_repo_scope = infer_scope
    if apply_scope is not None:
        _apply_github_repo_scope = apply_scope


# ---------------------------------------------------------------------------
# GitLab repo scope
# ---------------------------------------------------------------------------

GitlabRepoScope = tuple[str, str, str]
InferGitlabRepoScopeFn = Callable[
    [
        str,
        Sequence[tuple[str, str]] | None,
        Mapping[str, str] | None,
        str | Path | None,
        GitlabRepoScope | None,
    ],
    GitlabRepoScope | None,
]
ApplyGitlabRepoScopeFn = Callable[[dict[str, Any], str, str, str], dict[str, Any]]


def _default_infer_gitlab_scope(
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
    cached: GitlabRepoScope | None,
) -> GitlabRepoScope | None:
    _ = (message, conversation_messages, env, cwd, cached)
    return None


def _default_apply_gitlab_scope(
    resolved: dict[str, Any], project_id: str, ref_name: str, file_path: str
) -> dict[str, Any]:
    _ = (project_id, ref_name, file_path)
    return dict(resolved)


_infer_gitlab_repo_scope: InferGitlabRepoScopeFn = _default_infer_gitlab_scope
_apply_gitlab_repo_scope: ApplyGitlabRepoScopeFn = _default_apply_gitlab_scope


def infer_gitlab_repo_scope(
    *,
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    cached: GitlabRepoScope | None = None,
) -> GitlabRepoScope | None:
    return _infer_gitlab_repo_scope(message, conversation_messages, env, cwd, cached)


def apply_gitlab_repo_scope(
    resolved: dict[str, Any], project_id: str, ref_name: str, file_path: str
) -> dict[str, Any]:
    return _apply_gitlab_repo_scope(resolved, project_id, ref_name, file_path)


def set_gitlab_repo_scope_adapters(
    *,
    infer_scope: InferGitlabRepoScopeFn | None = None,
    apply_scope: ApplyGitlabRepoScopeFn | None = None,
) -> None:
    global _infer_gitlab_repo_scope, _apply_gitlab_repo_scope
    if infer_scope is not None:
        _infer_gitlab_repo_scope = infer_scope
    if apply_scope is not None:
        _apply_gitlab_repo_scope = apply_scope


# ---------------------------------------------------------------------------
# Test reset
# ---------------------------------------------------------------------------


def reset_harness_ports() -> None:
    """Restore all harness ports to noop defaults (tests)."""
    set_remote_integrations_fetcher(_default_fetch_remote)
    set_integration_resolution_adapters(
        load_integrations=_default_load_integrations,
        integration_store_path=_default_store_path,
        load_env_integrations=_default_load_env_integrations,
        classify_integrations=_default_classify_integrations,
        merge_local_integrations=_default_merge_local,
        merge_integrations_by_service=_default_merge_by_service,
        configured_services=_default_configured_services,
    )
    set_tool_registry(_EmptyToolRegistry())
    set_investigation_tools_adapter(get_investigation_tools=_default_investigation_tools)
    set_cli_llm_adapters(
        cli_provider_registration=_default_cli_provider_registration,
        build_cli_client=_cli_llm_backend_unavailable,
        flatten_cli_messages=_cli_llm_backend_unavailable,
    )
    set_github_repo_scope_adapters(
        infer_scope=_default_infer_github_scope,
        apply_scope=_default_apply_github_scope,
    )
    set_gitlab_repo_scope_adapters(
        infer_scope=_default_infer_gitlab_scope,
        apply_scope=_default_apply_gitlab_scope,
    )
