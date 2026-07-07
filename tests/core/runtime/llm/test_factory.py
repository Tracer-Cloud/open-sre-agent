"""Tests for the unified LLM factory (``core.llm.factory``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm.factory import LLMRole, LLMRoute, get_llm, reset_llm_clients, resolve_llm_route


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_llm_clients()
    yield
    reset_llm_clients()


def test_resolve_llm_route_reports_provider_and_sdk_transport(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "config.config.resolve_llm_settings", lambda: SimpleNamespace(provider="anthropic")
    )
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    route = resolve_llm_route()

    assert route.provider == "anthropic"
    assert route.use_litellm is False
    assert route.cli_provider_registration is None


def test_resolve_llm_route_azure_forces_litellm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "config.config.resolve_llm_settings", lambda: SimpleNamespace(provider="azure-openai")
    )
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    route = resolve_llm_route()

    # Azure always routes through LiteLLM even without the transport flag.
    assert route.use_litellm is True


def test_get_llm_routes_agent_and_non_agent_roles(monkeypatch: pytest.MonkeyPatch):
    route = LLMRoute(
        settings=SimpleNamespace(),
        provider="anthropic",
        cli_provider_registration=None,
        use_litellm=False,
    )
    monkeypatch.setattr("core.llm.factory.resolve_llm_route", lambda: route)
    monkeypatch.setattr(
        "core.llm.client_builders.build_agent_client", lambda _route: "AGENT_CLIENT"
    )
    monkeypatch.setattr(
        "core.llm.client_builders.build_reasoning_client",
        lambda _route, model_type: f"LLM:{model_type}",
    )

    assert get_llm(LLMRole.AGENT) == "AGENT_CLIENT"
    assert get_llm(LLMRole.REASONING) == "LLM:reasoning"
    assert get_llm(LLMRole.CLASSIFICATION) == "LLM:classification"
    assert get_llm(LLMRole.TOOLCALL) == "LLM:toolcall"


def test_get_llm_caches_per_role_and_invalidates_on_config_change(monkeypatch: pytest.MonkeyPatch):
    cache_key = {"value": ("sdk", "anthropic")}
    monkeypatch.setattr("core.llm.factory.current_llm_client_cache_key", lambda: cache_key["value"])
    monkeypatch.setattr(
        "core.llm.factory.resolve_llm_route",
        lambda: LLMRoute(SimpleNamespace(), "anthropic", None, False),
    )
    monkeypatch.setattr("core.llm.client_builders.build_agent_client", lambda _route: object())
    monkeypatch.setattr(
        "core.llm.client_builders.build_reasoning_client", lambda _route, _mt: object()
    )

    first_agent = get_llm(LLMRole.AGENT)
    assert get_llm(LLMRole.AGENT) is first_agent  # cached per role
    assert get_llm(LLMRole.REASONING) is not first_agent  # distinct role, distinct client

    cache_key["value"] = ("sdk", "openai")  # provider changed -> whole cache invalidates
    assert get_llm(LLMRole.AGENT) is not first_agent


def test_reset_llm_clients_forces_rebuild(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "core.llm.factory.current_llm_client_cache_key", lambda: ("sdk", "anthropic")
    )
    monkeypatch.setattr(
        "core.llm.factory.resolve_llm_route",
        lambda: LLMRoute(SimpleNamespace(), "anthropic", None, False),
    )
    monkeypatch.setattr("core.llm.client_builders.build_agent_client", lambda _route: object())
    monkeypatch.setattr(
        "core.llm.client_builders.build_reasoning_client", lambda _route, _mt: object()
    )

    first = get_llm(LLMRole.AGENT)
    reset_llm_clients()

    assert get_llm(LLMRole.AGENT) is not first
