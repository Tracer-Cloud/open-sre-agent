"""An out-of-tree integration has to survive the paths production actually uses.

Each test here drives a public entry point rather than the structure beneath it.
Asserting on ``EffectiveIntegrations`` or on ``registry.SUPPORTED_VERIFY_SERVICES``
directly would pass even when the resolver drops the key and when every importing
module is left holding a pre-registration snapshot.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from integrations import _catalog_impl, registry
from integrations.catalog import (
    load_env_integration_services,
    register_classifier,
    register_env_loader,
    register_env_presence,
    resolve_effective_integrations,
)
from integrations.registry import IntegrationSpec, register_integration_spec

SERVICE = "acme_external"
ENV_VAR = "ACME_EXTERNAL_API_KEY"


def _classify(credentials: dict[str, Any], record_id: str) -> tuple[Any | None, str | None]:
    return {"configured": True, "source": "local env"}, SERVICE


def _env_record() -> dict[str, Any] | None:
    if not os.getenv(ENV_VAR):
        return None
    return {
        "service": SERVICE,
        "status": "active",
        "source": "local env",
        "config": {"api_key": os.environ[ENV_VAR]},
    }


def _is_configured() -> bool:
    return bool(os.getenv(ENV_VAR))


@pytest.fixture
def registered_integration(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Register an external integration and remove it again afterwards.

    Registration mutates module-level tables, so leaking one would change the
    service lists every other test in the suite asserts against.
    """
    monkeypatch.setenv(ENV_VAR, "secret-value")

    register_integration_spec(
        IntegrationSpec(
            service=SERVICE,
            has_verifier=True,
            setup_order=99,
            direct_effective=True,
        )
    )
    register_classifier(SERVICE, _classify)
    register_env_loader(SERVICE, _env_record)
    register_env_presence(SERVICE, _is_configured)

    yield SERVICE

    registry._EXTERNAL_SPECS[:] = [
        spec for spec in registry._EXTERNAL_SPECS if spec.service != SERVICE
    ]
    registry._rebuild_registry()
    _catalog_impl._EXTERNAL_CLASSIFIERS.pop(SERVICE, None)
    _catalog_impl._EXTERNAL_ENV_LOADERS.pop(SERVICE, None)
    _catalog_impl._EXTERNAL_ENV_PRESENCE.pop(SERVICE, None)


def test_registration_reaches_a_module_that_imported_the_tables_earlier(
    registered_integration: str,
) -> None:
    """``integrations.verify`` binds the service lists at import time.

    It is imported long before a plugin registers, so a registration that
    replaced the tables instead of updating them would never be seen here.
    """
    import integrations.verify as verify

    assert registered_integration in verify.SUPPORTED_VERIFY_SERVICES


def test_registration_reaches_a_second_hand_re_export(registered_integration: str) -> None:
    """``integrations.app`` re-imports the list from ``integrations.verify``."""
    import integrations.app as app

    assert registered_integration in app.SUPPORTED_VERIFY_SERVICES


def test_external_service_survives_effective_resolution(registered_integration: str) -> None:
    """The resolver filters unknown keys before validating, so it must know this one."""
    effective = resolve_effective_integrations(
        store_integrations=[],
        env_integrations=[_env_record() or {}],
    )

    assert registered_integration in effective


def test_external_service_is_visible_to_the_startup_presence_check(
    registered_integration: str,
) -> None:
    """The pre-prompt check is a separate list from the full environment loader."""
    assert registered_integration in load_env_integration_services()


def test_presence_check_stays_quiet_when_the_integration_is_unconfigured(
    registered_integration: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)

    assert registered_integration not in load_env_integration_services()


def test_a_failing_presence_predicate_does_not_break_startup(
    registered_integration: str,
) -> None:
    """A plugin bug must degrade to "not configured", never to a failed launch."""

    def _explode() -> bool:
        raise RuntimeError("plugin is broken")

    _catalog_impl._EXTERNAL_ENV_PRESENCE[registered_integration] = _explode

    assert registered_integration not in load_env_integration_services()


def test_built_in_integrations_are_unaffected(registered_integration: str) -> None:
    import integrations.verify as verify

    assert "github" in verify.SUPPORTED_VERIFY_SERVICES
    assert len(verify.SUPPORTED_VERIFY_SERVICES) > 1


@pytest.fixture
def registered_with_setup_handler(registered_integration: str) -> Iterator[list[str]]:
    """Add the ``_HANDLERS`` entry a plugin appends, and record invocations."""
    import integrations.cli as cli

    calls: list[str] = []

    def _handler() -> None:
        calls.append(registered_integration)

    cli._HANDLERS[registered_integration] = _handler

    yield calls

    cli._HANDLERS.pop(registered_integration, None)


def test_setup_offers_a_registered_integration(registered_with_setup_handler: list[str]) -> None:
    """``setup_services`` was a tuple built at import from the registry and the
    handler map, so it could not see a plugin that arrives after either."""
    import integrations.cli as cli

    assert SERVICE in cli.setup_services()


def test_setup_dispatches_to_a_registered_integration(
    registered_with_setup_handler: list[str],
) -> None:
    """The gate in ``cmd_setup`` rejected an unknown service with ``_die``."""
    import integrations.cli as cli

    assert cli.cmd_setup(SERVICE) == SERVICE
    assert registered_with_setup_handler == [SERVICE]


def test_help_text_lists_a_registered_integration(
    registered_with_setup_handler: list[str],
) -> None:
    import integrations.cli as cli

    assert SERVICE in ", ".join(cli.setup_services())


@pytest.mark.parametrize(
    ("label", "spec"),
    [
        ("service name", IntegrationSpec(service="github", setup_order=999)),
        ("alias", IntegrationSpec(service="acme_alias", aliases=("github_mcp",))),
        (
            "family member",
            IntegrationSpec(service="acme_family", family_members=("grafana_local",)),
        ),
        ("alias over a service", IntegrationSpec(service="acme_over", aliases=("datadog",))),
    ],
)
def test_a_spec_cannot_claim_a_built_in_key(label: str, spec: IntegrationSpec) -> None:
    """Service names, aliases and family members all index the derived lookups.

    Letting a plugin claim any of them would point setup, verification,
    classification or family bucketing at the plugin instead of the built-in.
    """
    with pytest.raises(ValueError, match="built-in integration"):
        register_integration_spec(spec)


def test_a_rejected_spec_leaves_the_registry_untouched() -> None:
    before = dict(registry.INTEGRATION_SPECS_BY_SERVICE)

    with pytest.raises(ValueError):
        register_integration_spec(IntegrationSpec(service="github", setup_order=999))

    assert before == registry.INTEGRATION_SPECS_BY_SERVICE
    assert registry.INTEGRATION_SPECS_BY_SERVICE["github"].setup_order != 999
    assert "github" not in {spec.service for spec in registry._EXTERNAL_SPECS}


def test_built_in_lookups_still_resolve_after_a_registration(registered_integration: str) -> None:
    from integrations.registry import family_key, service_key

    assert service_key("github_mcp") == "github"
    assert family_key("grafana_local") == "grafana"
