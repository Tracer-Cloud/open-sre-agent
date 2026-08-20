"""Agent-harness ports — integrations, tools, and repository scope without tier violations.

Adapters register at process boot via
:func:`bootstrap.adapters.install_harness_adapters` (CLI, gateway, web, and
embedded profiles). Do not reintroduce a second registration site in a surface
or in :mod:`gateway.core.lifecycle.controller`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.strict_config import StrictConfigModel
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool

if TYPE_CHECKING:
    from core.agent_harness.ports import SubprocessPresenterFactory
    from core.tool import ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Integration resolution
# ---------------------------------------------------------------------------

RemoteIntegrationsFetcher = Callable[[str, str], list[dict[str, Any]]]
LoadIntegrationsFn = Callable[[], list[dict[str, Any]]]
IntegrationStorePathFn = Callable[[], str]
LoadEnvIntegrationsFn = Callable[[], list[dict[str, Any]]]
WebappVaultFetcherFn = Callable[[], list[dict[str, Any]] | None]
ClassifyIntegrationsFn = Callable[[list[dict[str, Any]]], dict[str, Any]]
MergeLocalIntegrationsFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]
]
MergeIntegrationsByServiceFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    list[dict[str, Any]],
]
ConfiguredIntegrationServicesFn = Callable[[], tuple[str, ...]]
SetupableIntegrationServicesFn = Callable[[], tuple[str, ...]]


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


def _default_setupable_services() -> tuple[str, ...]:
    return ()


def _default_fetch_webapp_vault() -> list[dict[str, Any]] | None:
    return None


_fetch_remote: RemoteIntegrationsFetcher = _default_fetch_remote
_load_integrations: LoadIntegrationsFn = _default_load_integrations
_store_path: IntegrationStorePathFn = _default_store_path
_load_env_integrations: LoadEnvIntegrationsFn = _default_load_env_integrations
_classify_integrations: ClassifyIntegrationsFn = _default_classify_integrations
_merge_local_integrations: MergeLocalIntegrationsFn = _default_merge_local
_merge_integrations_by_service: MergeIntegrationsByServiceFn = _default_merge_by_service
_configured_integration_services: ConfiguredIntegrationServicesFn = _default_configured_services
_setupable_integration_services: SetupableIntegrationServicesFn = _default_setupable_services
_fetch_webapp_vault: WebappVaultFetcherFn = _default_fetch_webapp_vault


def set_remote_integrations_fetcher(fetcher: RemoteIntegrationsFetcher) -> None:
    global _fetch_remote
    _fetch_remote = fetcher


def fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
    return _fetch_remote(org_id, auth_token)


def configured_integration_services() -> tuple[str, ...]:
    return _configured_integration_services()


def set_setupable_integration_services(fetcher: SetupableIntegrationServicesFn) -> None:
    """Register the catalog of service ids valid for ``/integrations setup``."""
    global _setupable_integration_services
    _setupable_integration_services = fetcher


def setupable_integration_services() -> tuple[str, ...]:
    """Service ids that have a real setup handler (never invent outside this set)."""
    return _setupable_integration_services()


def set_integration_resolution_adapters(
    *,
    load_integrations: LoadIntegrationsFn | None = None,
    integration_store_path: IntegrationStorePathFn | None = None,
    load_env_integrations: LoadEnvIntegrationsFn | None = None,
    classify_integrations: ClassifyIntegrationsFn | None = None,
    merge_local_integrations: MergeLocalIntegrationsFn | None = None,
    merge_integrations_by_service: MergeIntegrationsByServiceFn | None = None,
    configured_services: ConfiguredIntegrationServicesFn | None = None,
    fetch_webapp_vault: WebappVaultFetcherFn | None = None,
) -> None:
    global _load_integrations, _store_path, _load_env_integrations
    global _classify_integrations, _merge_local_integrations
    global _merge_integrations_by_service, _configured_integration_services
    global _fetch_webapp_vault
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
    if fetch_webapp_vault is not None:
        _fetch_webapp_vault = fetch_webapp_vault


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
            return _resolve_from_webapp_vault_or_local()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=env_token)
        except Exception:
            logger.debug(
                "Remote integrations fetch failed for org %s, falling back to local",
                org_id,
                exc_info=True,
            )
            return _resolve_from_webapp_vault_or_local()
        return _resolve_remote_with_local_fallback(all_integrations)

    return _resolve_from_webapp_vault_or_local()


def _resolve_from_webapp_vault_or_local() -> IntegrationResolutionResult:
    """Silo path: pull org vault from opensre-webapp, else local store/env.

    Merge order is vault → store → env so ops can still override a vault
    secret with ``GITHUB_MCP_AUTH_TOKEN`` (etc.) on the task definition.
    """
    remote = _fetch_webapp_vault()
    if remote is None:
        return _resolve_from_local_sources()
    if not remote:
        # Explicit empty vault — still allow local/env overlays (e.g. Slack SSM).
        return _resolve_from_local_sources()

    store_integrations = _load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        remote,
        store_integrations,
        env_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]
    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved integrations from webapp vault"
            f"{', store' if store_integrations else ''}"
            f"{', env' if env_integrations else ''}: {services}"
            if services
            else "No active integrations found"
        ),
    )


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

    def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
        return []

    def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
        return {}


def _default_investigation_tools(_resolved: dict[str, Any]) -> list[RegisteredTool]:
    return []


_tool_registry: ToolRegistry = _EmptyToolRegistry()
_get_investigation_tools: InvestigationToolsFn = _default_investigation_tools


def get_surface_tools(surface: ToolSurface) -> list[RegisteredTool]:
    return _tool_registry.tools_for_surface(surface)


def get_surface_tool_map(surface: ToolSurface) -> dict[str, RegisteredTool]:
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
# VCS repo scope
# ---------------------------------------------------------------------------
#
# Repository-scope inference (owner/repo, project/ref/file, …) is vendor
# behavior. Core must not name any specific VCS vendor — instead, each
# vendor's ``integrations`` package registers a ``VcsRepoScopeProvider`` that
# wraps its own infer/apply helpers.


@runtime_checkable
class VcsRepoScopeProvider(Protocol):
    """Adapter that infers and applies one vendor's repository scope.

    ``vendor`` names the cache slot in the session's scope bag (e.g.
    ``"github"``, ``"gitlab"``); ``infer``/``apply`` mirror the vendor's own
    scope helpers but speak in vendor-neutral ``tuple[str, ...]`` scopes.
    """

    vendor: str

    def infer(
        self,
        *,
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        """Resolve this vendor's repo scope from message/history/env/git/cache."""
        raise NotImplementedError

    def apply(self, resolved: dict[str, Any], scope: tuple[str, ...]) -> dict[str, Any]:
        """Return a copy of *resolved* enriched with this vendor's scope."""
        raise NotImplementedError


_vcs_repo_scope_providers: list[VcsRepoScopeProvider] = []


def register_vcs_repo_scope_provider(provider: VcsRepoScopeProvider) -> None:
    _vcs_repo_scope_providers.append(provider)


def clear_vcs_repo_scope_providers() -> None:
    _vcs_repo_scope_providers.clear()


def enrich_resolved_with_repo_scopes(
    *,
    resolved: dict[str, Any],
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
    cached_scopes: Mapping[str, tuple[str, ...]],
    set_cached_scope: Callable[[str, tuple[str, ...] | None], None] | None = None,
) -> dict[str, Any]:
    """Apply all registered VCS repo-scope providers to ``resolved``.

    Core callers must use this entrypoint instead of naming individual vendors.
    """
    out = dict(resolved)
    for provider in _vcs_repo_scope_providers:
        scope = provider.infer(
            message=message,
            conversation_messages=conversation_messages,
            env=env,
            cwd=cwd,
            cached=cached_scopes.get(provider.vendor),
        )
        if not scope:
            continue
        if set_cached_scope is not None:
            set_cached_scope(provider.vendor, scope)
        out = provider.apply(out, scope)
    return out


# ---------------------------------------------------------------------------
# Prompt vendor fragments
# ---------------------------------------------------------------------------
#
# Vendor-specific prompt paragraphs (tool usage recipes for a particular
# integration) do not belong in core's prompt builders. Integrations register
# a zero-arg fragment factory here from ``integrations/harness_adapters.py``;
# core prompt builders append the joined fragments without naming any vendor.

PromptFragmentFn = Callable[[], str]

_gather_prompt_fragments: list[PromptFragmentFn] = []
_action_prompt_fragments: list[PromptFragmentFn] = []
_assistant_prompt_fragments: list[PromptFragmentFn] = []


def register_gather_prompt_fragment(fn: PromptFragmentFn) -> None:
    _gather_prompt_fragments.append(fn)


def gather_prompt_vendor_fragments() -> str:
    return "\n".join(fn() for fn in _gather_prompt_fragments)


def clear_gather_prompt_fragments() -> None:
    _gather_prompt_fragments.clear()


def register_action_prompt_fragment(fn: PromptFragmentFn) -> None:
    _action_prompt_fragments.append(fn)


def action_prompt_vendor_fragments() -> str:
    return "\n\n".join(fn() for fn in _action_prompt_fragments)


def clear_action_prompt_fragments() -> None:
    _action_prompt_fragments.clear()


def register_assistant_prompt_fragment(fn: PromptFragmentFn) -> None:
    _assistant_prompt_fragments.append(fn)


def assistant_prompt_vendor_fragments() -> str:
    return "\n\n".join(fn() for fn in _assistant_prompt_fragments)


def clear_assistant_prompt_fragments() -> None:
    _assistant_prompt_fragments.clear()


# ---------------------------------------------------------------------------
# Gateway persona prompt fragments
# ---------------------------------------------------------------------------
#
# Slack/gateway teammate-persona wording (distinct from the vendor gather/
# action/assistant fragments above, which are joined into the shared prompt
# regardless of surface). Gateway persona fragments only apply when the
# turn's surface is "gateway"; core prompt builders must not import the
# vendor module that owns the wording.

_gateway_persona_fragments: list[PromptFragmentFn] = []


def register_gateway_persona_fragment(fn: PromptFragmentFn) -> None:
    _gateway_persona_fragments.append(fn)


def gateway_persona_fragments() -> str:
    return "\n\n".join(fn() for fn in _gateway_persona_fragments)


def clear_gateway_persona_fragments() -> None:
    _gateway_persona_fragments.clear()


# ---------------------------------------------------------------------------
# Message context prefix strippers
# ---------------------------------------------------------------------------
#
# Gateway surfaces (Slack, etc.) may prepend a channel/context marker to the
# raw message text (e.g. ``[Slack channel_id=…]``) before it reaches core
# prompt/turn helpers. Core needs to strip that marker to evaluate the
# underlying text (for example, matching a bare affirmative like "yes")
# without hardcoding any vendor's marker format.

MessageContextPrefixStripper = Callable[[str], "tuple[str, str] | None"]

_message_context_prefix_strippers: list[MessageContextPrefixStripper] = []


def register_message_context_prefix_stripper(fn: MessageContextPrefixStripper) -> None:
    _message_context_prefix_strippers.append(fn)


def clear_message_context_prefix_strippers() -> None:
    _message_context_prefix_strippers.clear()


def strip_message_context_prefix(text: str) -> tuple[str, str]:
    """Return ``(prefix, remainder)``, trying registered strippers in order.

    The first stripper to return a non-``None`` result wins. Falls back to
    ``("", text)`` when no registered stripper matches.
    """
    for stripper in _message_context_prefix_strippers:
        result = stripper(text)
        if result is not None:
            return result
    return "", text


# ---------------------------------------------------------------------------
# Preferred evidence sources (by ask kind)
# ---------------------------------------------------------------------------
#
# Core classifies ask kinds (``metric_read``, …) but must not name vendor
# integration ids. Each analytics/vendor package *opts in* at boot by appending
# its service id. Nothing is preferred until a vendor registers — omitting a
# vendor's registration means metric asks will not CTA that vendor.

_preferred_evidence_sources: dict[str, tuple[str, ...]] = {}


def register_preferred_evidence_source(kind: str, *service_ids: str) -> None:
    """Opt service id(s) into satisfying asks of ``kind`` (append, dedupe).

    Vendors call this from their own modules. The composition root must not
    invent a default vendor list in core — only wire who opts in.
    """
    if not service_ids:
        return
    current = _preferred_evidence_sources.get(kind, ())
    merged = list(current)
    for service_id in service_ids:
        if service_id and service_id not in merged:
            merged.append(service_id)
    _preferred_evidence_sources[kind] = tuple(merged)


def preferred_evidence_sources_for(kind: str) -> tuple[str, ...]:
    """Return preferred integration ids for ``kind``, or ``()`` when none opted in."""
    return _preferred_evidence_sources.get(kind, ())


def preferred_evidence_sources_by_kind() -> dict[str, tuple[str, ...]]:
    """Return a copy of every kind → preferred service ids map."""
    return {kind: ids for kind, ids in _preferred_evidence_sources.items() if ids}


def clear_preferred_evidence_sources() -> None:
    _preferred_evidence_sources.clear()


# ---------------------------------------------------------------------------
# Metric query drafts + vendor cohort resolvers
# ---------------------------------------------------------------------------
#
# Core owns *when* an unformed metric answer needs a draft fence + setup slash
# and *when* a SessionGoal is about people-cohort identity
# (:mod:`core.agent_harness.turns.cohort_identity`). Draft text, which tools
# run a query, which targets are schema discovery, and how to read a vendor's
# observations for "cohort resolved" are integration-owned and opt in here.
# With no draft registered, core uses a generic text fence.
#
# A source that registers only a draft is still second-class: core cannot tell
# its query tools from its schema probes. Register the tools too.

MetricCohortResolvedFn = Callable[[Any, str], bool]
"""``(evidence, reply) -> True`` when a vendor cohort is live-resolved."""


@dataclass(frozen=True, slots=True)
class MetricQueryDraft:
    """Draft fences one analytics source offers when no live query formed."""

    count: str
    cohort: str | None
    priority: int


_metric_query_drafts: dict[str, MetricQueryDraft] = {}
_metric_cohort_resolvers: dict[str, MetricCohortResolvedFn] = {}
_metric_query_tools: dict[str, frozenset[str]] = {}
_metric_discovery_targets: dict[str, frozenset[str]] = {}


def _require_service_id(service_id: str, *, port: str) -> str:
    """Return the trimmed id, or raise — a blank id is a wiring bug, not input."""
    key = (service_id or "").strip()
    if not key:
        raise ValueError(f"{port} needs a non-empty service_id")
    return key


def register_metric_query_tools(service_id: str, tools: Collection[str]) -> None:
    """Register the tool names that run a live query for this source.

    Without this, core falls back to shape rules that recognise no vendor, so
    the source's real queries read as "no metric query ran".
    """
    key = _require_service_id(service_id, port="register_metric_query_tools")
    names = frozenset(name.strip().lower() for name in tools if name and name.strip())
    if not names:
        raise ValueError(f"register_metric_query_tools({key!r}) needs at least one tool name")
    _metric_query_tools[key] = _metric_query_tools.get(key, frozenset()) | names


def register_discovery_targets(service_id: str, targets: Collection[str]) -> None:
    """Register the bridge targets that only explore schema for this source."""
    key = _require_service_id(service_id, port="register_discovery_targets")
    names = frozenset(name.strip().lower() for name in targets if name and name.strip())
    if not names:
        raise ValueError(f"register_discovery_targets({key!r}) needs at least one target")
    _metric_discovery_targets[key] = _metric_discovery_targets.get(key, frozenset()) | names


def registered_metric_query_tools() -> frozenset[str]:
    """Every tool name any source registered as running a live query."""
    return frozenset().union(*_metric_query_tools.values()) if _metric_query_tools else frozenset()


def registered_discovery_targets() -> frozenset[str]:
    """Every bridge target any source registered as schema discovery."""
    if not _metric_discovery_targets:
        return frozenset()
    return frozenset().union(*_metric_discovery_targets.values())


def register_metric_query_draft(
    service_id: str,
    *,
    count_draft: str,
    cohort_draft: str | None = None,
    priority: int = 50,
) -> None:
    """Register the draft fence(s) this analytics source owns.

    ``count_draft`` / ``cohort_draft`` are full markdown fences. ``cohort_draft``
    is optional — omit it when the vendor has no cohort/signup template. Lower
    ``priority`` wins when several registered sources are connected at once.
    """
    key = _require_service_id(service_id, port="register_metric_query_draft")
    count = (count_draft or "").strip()
    if not count:
        raise ValueError(f"register_metric_query_draft({key!r}) needs a non-empty count_draft")
    cohort = (
        cohort_draft.strip() if isinstance(cohort_draft, str) and cohort_draft.strip() else None
    )
    _metric_query_drafts[key] = MetricQueryDraft(count=count, cohort=cohort, priority=priority)


def register_metric_cohort_resolver(service_id: str, resolver: MetricCohortResolvedFn) -> None:
    """Register how this source decides a cohort is resolved from gather evidence.

    Used after a live query ran: core asks whether identity is still open so it
    can keep the draft fence. The vendor owns observation parsers; core must not.
    """
    key = _require_service_id(service_id, port="register_metric_cohort_resolver")
    _metric_cohort_resolvers[key] = resolver


def metric_query_draft_for(
    service_ids: tuple[str, ...],
    *,
    cohort_goal: bool = False,
) -> str | None:
    """Return the registered draft for the highest-priority connected source.

    Ranked by registration priority, then service id — never by the order the
    caller happened to assemble ``service_ids``, which made the winner depend
    on an unrelated tuple.
    """
    matches = [
        (draft, key)
        for key in {str(raw or "").strip() for raw in service_ids}
        if key and (draft := _metric_query_drafts.get(key)) is not None
    ]
    if not matches:
        return None
    draft, _key = min(matches, key=lambda pair: (pair[0].priority, pair[1]))
    if cohort_goal and draft.cohort is not None:
        return draft.cohort
    return draft.count


def metric_cohort_resolved_for(
    service_ids: tuple[str, ...],
    evidence: Any,
    reply: str,
) -> bool | None:
    """Ask registered resolvers whether cohort identity is resolved.

    Returns ``None`` when no opted-in source has a resolver (caller falls back
    to reply-only signals). ``True``/``False`` from the first matching source.
    """
    for raw in service_ids:
        key = str(raw or "").strip()
        if not key:
            continue
        resolver = _metric_cohort_resolvers.get(key)
        if resolver is not None:
            return bool(resolver(evidence, reply))
    return None


def clear_metric_query_drafts() -> None:
    _metric_query_drafts.clear()
    _metric_cohort_resolvers.clear()
    _metric_query_tools.clear()
    _metric_discovery_targets.clear()


# ---------------------------------------------------------------------------
# Test reset
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration setup command (surface syntax)
# ---------------------------------------------------------------------------
#
# Core builds the upgrade CTA but must not know slash syntax. The surface
# registers how it spells "connect this integration" at boot.


def _default_integration_setup_command(service_id: str) -> str:
    return f"integrations setup {service_id}"


_integration_setup_command: Callable[[str], str] = _default_integration_setup_command


def set_integration_setup_command(render: Callable[[str], str]) -> None:
    """Register how this surface spells the connect command for a service."""
    global _integration_setup_command
    _integration_setup_command = render


def integration_setup_command(service_id: str) -> str:
    """Return the surface command that connects ``service_id``."""
    return _integration_setup_command(service_id)


def reset_harness_ports() -> None:
    """Restore all harness ports to noop defaults (tests).

    Also clears core leaf registries that integrations register through
    :func:`integrations.harness_adapters.register_harness_adapters` (alert
    routing, taxonomy profiles, anchor parsers, detail fields, …). Without
    those clears, tests that call ``reset_harness_ports()`` mid-suite would
    keep stale vendor registrations while VCS/prompt ports look empty —
    a silent inconsistency.
    """
    set_remote_integrations_fetcher(_default_fetch_remote)
    set_integration_resolution_adapters(
        load_integrations=_default_load_integrations,
        integration_store_path=_default_store_path,
        load_env_integrations=_default_load_env_integrations,
        classify_integrations=_default_classify_integrations,
        merge_local_integrations=_default_merge_local,
        merge_integrations_by_service=_default_merge_by_service,
        configured_services=_default_configured_services,
        fetch_webapp_vault=_default_fetch_webapp_vault,
    )
    set_tool_registry(_EmptyToolRegistry())
    set_investigation_tools_adapter(get_investigation_tools=_default_investigation_tools)
    set_cli_llm_adapters(
        cli_provider_registration=_default_cli_provider_registration,
        build_cli_client=_cli_llm_backend_unavailable,
        flatten_cli_messages=_cli_llm_backend_unavailable,
    )
    clear_vcs_repo_scope_providers()
    clear_gather_prompt_fragments()
    clear_action_prompt_fragments()
    clear_assistant_prompt_fragments()
    clear_gateway_persona_fragments()
    clear_message_context_prefix_strippers()
    clear_preferred_evidence_sources()
    clear_metric_query_drafts()
    set_subprocess_presenter_factory(None)
    set_integration_setup_command(_default_integration_setup_command)
    set_setupable_integration_services(_default_setupable_services)

    # Core leaf registries (populated by integrations/harness_adapters).
    from core.domain.alerts.alert_source import (
        clear_alert_source_detectors,
        clear_alert_source_routing,
        clear_secondary_tool_sources,
        clear_source_aliases,
    )
    from core.domain.alerts.extraction import clear_alert_detail_fields
    from core.domain.diagnosis.taxonomy_registry import clear_taxonomy_profiles
    from core.domain.types.incident_anchors import clear_anchor_parsers

    clear_alert_source_detectors()
    clear_alert_source_routing()
    clear_source_aliases()
    clear_secondary_tool_sources()
    clear_alert_detail_fields()
    clear_taxonomy_profiles()
    clear_anchor_parsers()


# --- Subprocess presenter -------------------------------------------------
#
# Streaming shell/CLI output needs a presenter that lives in ``tools`` (its
# process helpers) and is therefore invisible to ``core.agent_harness``.
# Registering it here lets the default agent execute shell tools instead of
# refusing them, without core importing tools or gateway.

_subprocess_presenter_factory: SubprocessPresenterFactory | None = None


def set_subprocess_presenter_factory(factory: SubprocessPresenterFactory | None) -> None:
    """Register (or clear) the presenter the default agent gives shell tools."""
    global _subprocess_presenter_factory
    _subprocess_presenter_factory = factory


def get_subprocess_presenter_factory() -> SubprocessPresenterFactory | None:
    """Return the registered presenter factory, or ``None`` before boot."""
    return _subprocess_presenter_factory
