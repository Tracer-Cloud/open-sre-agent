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
    _forget_hooks(SERVICE)


def _forget_hooks(service: str) -> None:
    """Drop every hook registered for *service*, ownership record included."""
    _catalog_impl._EXTERNAL_CLASSIFIERS.pop(service, None)
    _catalog_impl._EXTERNAL_ENV_LOADERS.pop(service, None)
    _catalog_impl._EXTERNAL_ENV_PRESENCE.pop(service, None)
    for hook, key in list(_catalog_impl._EXTERNAL_HOOK_OWNERS):
        if key == service:
            del _catalog_impl._EXTERNAL_HOOK_OWNERS[hook, key]


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


@pytest.fixture
def first_plugin() -> Iterator[str]:
    """Register one external integration so a second one can collide with it."""
    register_integration_spec(
        IntegrationSpec(
            service="plugin_a", aliases=("shared_alias",), family_members=("shared_kid",)
        )
    )

    yield "plugin_a"

    registry._EXTERNAL_SPECS[:] = [
        spec for spec in registry._EXTERNAL_SPECS if spec.service != "plugin_a"
    ]
    registry._rebuild_registry()


@pytest.mark.parametrize(
    ("label", "spec"),
    [
        ("alias", IntegrationSpec(service="plugin_b", aliases=("shared_alias",))),
        ("family member", IntegrationSpec(service="plugin_c", family_members=("shared_kid",))),
        ("alias over their service", IntegrationSpec(service="plugin_d", aliases=("plugin_a",))),
        ("service over their alias", IntegrationSpec(service="shared_alias")),
    ],
)
def test_a_spec_cannot_claim_another_plugins_key(
    first_plugin: str, label: str, spec: IntegrationSpec
) -> None:
    """Two plugins installed together must not silently fight over a key.

    Whichever imported last would otherwise win, with nothing reporting it.
    """
    with pytest.raises(ValueError, match="another registered integration"):
        register_integration_spec(spec)


def test_re_registering_the_same_service_still_replaces_it(first_plugin: str) -> None:
    """A reload re-registers the same spec; its own keys are not a self-conflict."""
    from integrations.registry import service_key

    register_integration_spec(
        IntegrationSpec(service=first_plugin, aliases=("shared_alias",), setup_order=5)
    )

    assert service_key("shared_alias") == first_plugin
    assert registry.INTEGRATION_SPECS_BY_SERVICE[first_plugin].setup_order == 5
    assert [spec.service for spec in registry._EXTERNAL_SPECS].count(first_plugin) == 1


@pytest.mark.parametrize(
    ("hook", "register"),
    [
        ("classifier", lambda: register_classifier("github", _classify)),
        ("env loader", lambda: register_env_loader("github", _env_record)),
        ("presence check", lambda: register_env_presence("github", _is_configured)),
    ],
)
def test_a_hook_cannot_attach_to_a_built_in_service(hook: str, register: Any) -> None:
    """Each hook can be called without a spec, so each needs the same gate."""
    with pytest.raises(ValueError, match="built-in integration"):
        register()


def test_an_env_loader_cannot_emit_another_integrations_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The merge replaces by service, so an impostor record would outrank the real one."""
    monkeypatch.setenv("GITHUB_MCP_URL", "https://real.example")

    def _impostor() -> dict[str, Any]:
        return {
            "service": "github",
            "status": "active",
            "source": "local env",
            "config": {"url": "https://impostor.example"},
        }

    register_env_loader("acme_impostor", _impostor)
    try:
        github = [r for r in _catalog_impl.load_env_integrations() if r.get("service") == "github"]
    finally:
        _forget_hooks("acme_impostor")

    assert len(github) == 1
    assert "impostor" not in str(github[0].get("config"))


def test_the_cli_command_offers_a_registered_integration(
    registered_with_setup_handler: list[str],
) -> None:
    """``click.Choice`` captured the service list when the decorator ran.

    The command module is imported during CLI startup, before any plugin has
    registered, so a captured list rejected the plugin with "is not one of"
    before ``cmd_setup`` was reached.
    """
    from surfaces.cli.commands.integrations import integrations as integrations_group

    setup_command = integrations_group.get_command(None, "setup")
    assert setup_command is not None
    assert SERVICE in list(setup_command.params[0].type.choices)


def test_the_cli_command_still_rejects_an_unknown_service() -> None:
    from click.testing import CliRunner

    from surfaces.cli.commands.integrations import integrations as integrations_group

    result = CliRunner().invoke(integrations_group, ["setup", "definitely_not_a_service"])

    assert result.exit_code == 2


@pytest.mark.parametrize("name", ["GitHub", " github", "github ", "GITHUB_MCP"])
def test_a_built_in_key_is_claimed_whatever_the_casing(name: str) -> None:
    """Readers strip and lower-case, so a raw string comparison would let these through."""
    with pytest.raises(ValueError, match="built-in integration"):
        register_integration_spec(IntegrationSpec(service=name, has_verifier=True))


@pytest.mark.parametrize("name", ["", "   "])
def test_a_spec_needs_a_service_name(name: str) -> None:
    with pytest.raises(ValueError, match="non-empty service name"):
        register_integration_spec(IntegrationSpec(service=name))


def test_keys_are_stored_in_the_form_the_lookups_are_queried_with() -> None:
    from integrations.registry import service_key

    register_integration_spec(IntegrationSpec(service="  MixedCase  ", aliases=("Some Alias ",)))
    try:
        assert service_key("MIXEDCASE") == "mixedcase"
        assert service_key("some alias") == "mixedcase"
        assert "mixedcase" in registry.INTEGRATION_SPECS_BY_SERVICE
    finally:
        registry._EXTERNAL_SPECS[:] = [
            spec for spec in registry._EXTERNAL_SPECS if spec.service != "mixedcase"
        ]
        registry._rebuild_registry()


def test_a_rebuilt_table_never_drops_a_surviving_key() -> None:
    """A reader must not catch the tables mid-rebuild.

    ``service_key`` falls back to the identity on a miss, so a key that vanished
    for an instant would resolve an alias to itself and route to the wrong
    integration.
    """
    observed: list[bool] = []
    table = {"keep": "keep", "drop": "drop"}

    class _Watcher(dict[str, str]):
        def __delitem__(self, key: str) -> None:
            observed.append("keep" in self)
            super().__delitem__(key)

    watched = _Watcher(table)
    registry._refill_mapping(watched, {"keep": "keep", "added": "added"})

    assert all(observed), "a surviving key disappeared while the table was rebuilt"
    assert watched == {"keep": "keep", "added": "added"}


def _make_plugin_module(name: str) -> Any:
    """Return a throwaway module standing in for a separately installed plugin.

    Ownership is inferred from where the callback was defined, so two plugins
    have to come from two modules for the check to mean anything.
    """
    import sys
    import types

    module = types.ModuleType(name)
    exec(  # noqa: S102 - building a stand-in plugin module is the point
        "def classify(credentials, record_id):\n"
        "    return None, None\n"
        "def load():\n"
        "    return None\n"
        "def present():\n"
        "    return True\n",
        module.__dict__,
    )
    sys.modules[name] = module
    return module


@pytest.fixture
def two_plugin_modules() -> Iterator[tuple[Any, Any]]:
    import sys

    alpha = _make_plugin_module("plugin_alpha_stub")
    beta = _make_plugin_module("plugin_beta_stub")

    yield alpha, beta

    _forget_hooks("shared_hook_key")
    sys.modules.pop("plugin_alpha_stub", None)
    sys.modules.pop("plugin_beta_stub", None)


@pytest.mark.parametrize(
    ("hook", "attribute", "register"),
    [
        ("classifier", "classify", register_classifier),
        ("env loader", "load", register_env_loader),
        ("presence check", "present", register_env_presence),
    ],
)
def test_a_second_plugin_cannot_take_over_a_hook(
    two_plugin_modules: tuple[Any, Any], hook: str, attribute: str, register: Any
) -> None:
    """Assignment into the hook map was last-write-wins, settled by import order."""
    alpha, beta = two_plugin_modules

    register("shared_hook_key", getattr(alpha, attribute))

    with pytest.raises(ValueError, match="already registered by"):
        register("shared_hook_key", getattr(beta, attribute))


def test_the_owning_plugin_can_re_register_its_own_hook(
    two_plugin_modules: tuple[Any, Any],
) -> None:
    """A reload hands back a new function object from the same package."""
    alpha, _beta = two_plugin_modules

    register_classifier("shared_hook_key", alpha.classify)
    exec(  # noqa: S102 - stands in for the module being reloaded
        "def classify(credentials, record_id):\n    return None, None\n", alpha.__dict__
    )
    register_classifier("shared_hook_key", alpha.classify)

    assert _catalog_impl._EXTERNAL_CLASSIFIERS["shared_hook_key"] is alpha.classify


def test_an_unattributable_hook_cannot_overwrite_an_existing_one() -> None:
    """A callable with no package of its own identifies nobody, so it may not replace."""
    register_env_loader("unattributable_key", dict)
    try:
        with pytest.raises(ValueError, match="already registered by"):
            register_env_loader("unattributable_key", list)
    finally:
        _forget_hooks("unattributable_key")


def test_only_one_package_wins_a_contested_hook_key() -> None:
    """Claiming and installing must be one step.

    Reading the owner and then writing lets every concurrent caller see the key
    free, so the last writer takes it and the others never learn they lost.
    """
    import threading

    modules = [_make_plugin_module(f"contender_{index}_stub") for index in range(8)]
    won: list[str] = []
    refused: list[str] = []
    barrier = threading.Barrier(len(modules))

    def claim(module: Any) -> None:
        barrier.wait()
        try:
            register_classifier("contested_key", module.classify)
            won.append(module.__name__)
        except ValueError:
            refused.append(module.__name__)

    threads = [threading.Thread(target=claim, args=(module,)) for module in modules]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert len(won) == 1, f"expected a single winner, got {won}"
        assert len(refused) == len(modules) - 1
        owner = _catalog_impl._EXTERNAL_HOOK_OWNERS["a classifier", "contested_key"]
        assert owner == won[0]
    finally:
        import sys

        _forget_hooks("contested_key")
        for module in modules:
            sys.modules.pop(module.__name__, None)


def test_the_presence_sweep_survives_a_registration_from_another_thread() -> None:
    """``load_env_integration_services`` iterates the hook map during startup."""
    import sys
    import threading

    module = _make_plugin_module("sweeper_stub")
    crashes: list[str] = []
    stop = threading.Event()

    def scan() -> None:
        while not stop.is_set():
            try:
                load_env_integration_services()
            except RuntimeError as exc:  # "dictionary changed size during iteration"
                crashes.append(str(exc))

    scanner = threading.Thread(target=scan)
    scanner.start()
    registered = [f"sweep_target_{index}" for index in range(200)]
    try:
        for service in registered:
            register_env_presence(service, module.present)
    finally:
        stop.set()
        scanner.join()
        for service in registered:
            _forget_hooks(service)
        sys.modules.pop("sweeper_stub", None)

    assert not crashes
