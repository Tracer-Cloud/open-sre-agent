"""Tests for the verification plugin registry (issue #37).

Covers:

* The registry mechanism itself — register / get / list semantics.
* The two factory helpers (``build_probe_verifier`` /
  ``build_validation_verifier``) including all error branches.
* The shorthand registration helpers (``register_probe_verifier`` /
  ``register_validation_verifier``).
* Catalog sync — every service in ``SUPPORTED_VERIFY_SERVICES`` resolves
  via the registry.
* The Supabase preserved arg-swap behavior — pinned so any "fix the
  typo" PR has to consciously break this test.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations._verifiers_loader import register_all_verifiers
from app.integrations.registry import SUPPORTED_VERIFY_SERVICES
from app.integrations.verification import (
    build_probe_verifier,
    build_validation_verifier,
    get_verifier,
    list_verifiers,
    register_probe_verifier,
    register_validation_verifier,
    register_verifier,
    result,
)
from app.integrations.verification.registry import (
    _reset_for_testing,
    _restore_for_testing,
    _snapshot_for_testing,
)


@pytest.fixture(autouse=True, scope="module")
def _ensure_verifiers_registered() -> None:
    """Trigger the loader once for the whole module so the test suite
    runs against a populated registry regardless of import order."""
    register_all_verifiers()


@pytest.fixture
def _isolated_registry() -> Iterator[None]:
    """Snapshot + restore the registry around each test."""
    snapshot = _snapshot_for_testing()
    try:
        yield
    finally:
        _restore_for_testing(snapshot)


class TestRegistry:
    def test_register_returns_decorated_function_unchanged(self, _isolated_registry: None) -> None:
        def _vendor_verifier(source: str, config: dict[str, Any]) -> dict[str, str]:
            return result("fake-vendor", source, "passed", "ok")

        decorated = register_verifier("fake-vendor")(_vendor_verifier)
        assert decorated is _vendor_verifier

    def test_get_verifier_returns_the_registered_function(self, _isolated_registry: None) -> None:
        def _vendor_verifier(source: str, config: dict[str, Any]) -> dict[str, str]:
            return result("fake-vendor", source, "passed", "ok")

        register_verifier("fake-vendor")(_vendor_verifier)
        assert get_verifier("fake-vendor") is _vendor_verifier

    def test_get_verifier_returns_none_for_unknown_service(self, _isolated_registry: None) -> None:
        assert get_verifier("does-not-exist") is None

    def test_list_verifiers_sorted_includes_registered(self, _isolated_registry: None) -> None:
        _reset_for_testing()

        def _verify_b(source: str, _config: dict[str, Any]) -> dict[str, str]:
            return result("b", source, "passed", "")

        def _verify_a(source: str, _config: dict[str, Any]) -> dict[str, str]:
            return result("a", source, "passed", "")

        register_verifier("b")(_verify_b)
        register_verifier("a")(_verify_a)
        assert list_verifiers() == ["a", "b"]

    def test_re_registering_replaces_silently(self, _isolated_registry: None) -> None:
        """Re-importing a verifier module (e.g. test reloads) must not raise."""

        def _first(source: str, _config: dict[str, Any]) -> dict[str, str]:
            return result("dup", source, "passed", "first")

        def _second(source: str, _config: dict[str, Any]) -> dict[str, str]:
            return result("dup", source, "passed", "second")

        register_verifier("dup")(_first)
        register_verifier("dup")(_second)
        assert get_verifier("dup") is _second


class TestRegistryCatalogSync:
    def test_list_verifiers_matches_supported_services(self) -> None:
        """Every CLI/catalog service has a registered verifier."""
        assert set(list_verifiers()) == set(SUPPORTED_VERIFY_SERVICES)


class TestBuildProbeVerifier:
    def test_returns_passed_when_config_valid_and_probe_passes(self) -> None:
        class _OkProbe:
            status = "passed"
            detail = "all good"

        class _Client:
            def __init__(self, _cfg: Any) -> None:
                pass

            def probe_access(self) -> _OkProbe:
                return _OkProbe()

        verifier = build_probe_verifier(
            "fake",
            build_config=lambda raw: {"normalized": raw},
            client_factory=_Client,
        )
        assert verifier("local store", {"key": "val"}) == {
            "service": "fake",
            "source": "local store",
            "status": "passed",
            "detail": "all good",
        }

    def test_returns_missing_when_config_build_raises(self) -> None:
        def _bad_build(_raw: dict[str, Any]) -> None:
            raise ValueError("missing required field 'token'")

        verifier = build_probe_verifier(
            "fake",
            build_config=_bad_build,
            client_factory=lambda _cfg: None,
        )
        assert verifier("local store", {})["status"] == "missing"
        assert "missing required field" in verifier("local store", {})["detail"]

    def test_returns_failed_when_probe_raises(self) -> None:
        class _Client:
            def __init__(self, _cfg: Any) -> None:
                pass

            def probe_access(self) -> Any:
                raise RuntimeError("network down")

        verifier = build_probe_verifier(
            "fake",
            build_config=lambda raw: raw,
            client_factory=_Client,
        )
        assert verifier("local store", {})["status"] == "failed"
        assert "network down" in verifier("local store", {})["detail"]


class TestBuildValidationVerifier:
    def test_returns_missing_when_build_config_raises(self) -> None:
        def _bad_build(_raw: dict[str, Any]) -> None:
            raise ValueError("missing required field 'host'")

        verifier = build_validation_verifier(
            "fake",
            build_config=_bad_build,
            validate_config=lambda _cfg: SimpleNamespace(ok=True, detail=""),
        )
        out = verifier("local store", {})
        assert out["status"] == "missing"
        assert "missing required field" in out["detail"]

    def test_returns_failed_when_validate_raises(self) -> None:
        def _validate_raises(_cfg: Any) -> None:
            raise RuntimeError("validation blew up")

        verifier = build_validation_verifier(
            "fake",
            build_config=lambda raw: raw,
            validate_config=_validate_raises,
        )
        out = verifier("local store", {})
        assert out["status"] == "failed"
        assert "validation blew up" in out["detail"]


class TestShorthandRegistration:
    def test_register_probe_verifier_registers_and_returns_callable(
        self, _isolated_registry: None
    ) -> None:
        class _Client:
            def __init__(self, _cfg: Any) -> None:
                pass

            def probe_access(self) -> Any:
                return SimpleNamespace(status="passed", detail="ok")

        fn = register_probe_verifier(
            "shorthand-probe",
            config=lambda raw: raw,
            client=_Client,
        )
        assert get_verifier("shorthand-probe") is fn
        assert fn("local store", {})["status"] == "passed"

    def test_register_validation_verifier_registers_and_returns_callable(
        self, _isolated_registry: None
    ) -> None:
        fn = register_validation_verifier(
            "shorthand-validate",
            build_config=lambda raw: raw,
            validate_config=lambda _cfg: SimpleNamespace(ok=True, detail="config looks fine"),
        )
        assert get_verifier("shorthand-validate") is fn
        assert fn("local store", {})["status"] == "passed"


class TestLoaderAutoDiscovery:
    """Pin the loader's contract: ``register_all_verifiers()`` populates
    the registry from both vendor locations and is safe to call twice."""

    def test_calling_register_all_verifiers_populates_the_registry(self) -> None:
        register_all_verifiers()
        # The catalog declares which services must have verifiers; auto-
        # discovery's job is to make every one of them resolvable.
        for service in SUPPORTED_VERIFY_SERVICES:
            assert get_verifier(service) is not None, (
                f"auto-discovery missed {service!r} — either the verifier "
                "module is misnamed or the loader's walk excluded it"
            )

    def test_register_all_verifiers_is_idempotent(self, _isolated_registry: None) -> None:
        """Re-registration must replace silently (no exception) so that
        repeated calls from different entry points don't blow up."""
        register_all_verifiers()
        before = sorted(list_verifiers())
        register_all_verifiers()  # second call must not raise
        after = sorted(list_verifiers())
        assert before == after


class TestVerifyWithValidationResult:
    """Direct tests for ``verify_with_validation_result`` — exercised
    indirectly through ``build_validation_verifier`` elsewhere, but
    worth pinning the contract directly so future refactors of the
    factory don't quietly change the helper's behavior."""

    def test_passes_when_validation_ok(self) -> None:
        from app.integrations.verification import verify_with_validation_result

        out = verify_with_validation_result(
            "fake",
            "local store",
            {"raw": 1},
            build_config=lambda raw: raw,
            validate_config=lambda _cfg: SimpleNamespace(ok=True, detail="all good"),
        )
        assert out == {
            "service": "fake",
            "source": "local store",
            "status": "passed",
            "detail": "all good",
        }

    def test_fails_when_validation_not_ok(self) -> None:
        from app.integrations.verification import verify_with_validation_result

        out = verify_with_validation_result(
            "fake",
            "local env",
            {},
            build_config=lambda raw: raw,
            validate_config=lambda _cfg: SimpleNamespace(ok=False, detail="bad token"),
        )
        assert out["status"] == "failed"
        assert out["detail"] == "bad token"


class TestAlertmanagerRegistration:
    def test_alertmanager_is_registered_via_canonical_name(self) -> None:
        """Import the vendor verifier module and check it's reachable
        through both the registry and the module-level export."""
        from app.services.alertmanager.verifier import verify_alertmanager

        assert get_verifier("alertmanager") is verify_alertmanager


class TestSupabasePreservedArgSwap:
    """Pin the pre-#37 arg-name swap in ``verify_supabase``.

    The original ``_verify_supabase`` passed its first positional arg
    as ``service`` (not ``source``) and the literal ``"supabase"`` as
    ``source``. That's almost certainly a typo. This test locks in
    the existing behavior so any "fix" PR has to consciously break it
    and ship a separate ticket.
    """

    def test_first_arg_lands_in_service_field_and_source_is_supabase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations import verifiers as _verifiers_pkg
        from app.integrations.verifiers import supabase as _supabase_verifier

        # Bypass the real config validation chain — we're testing the
        # verifier's arg routing, not Supabase config parsing.
        monkeypatch.setattr(
            _supabase_verifier,
            "build_supabase_config",
            lambda raw: raw,
        )
        monkeypatch.setattr(
            _supabase_verifier,
            "validate_supabase_config",
            lambda _cfg: SimpleNamespace(ok=True, detail="ok"),
        )
        # Reach the registered callable via the registry so we test
        # exactly what the dispatcher invokes.
        verifier = get_verifier("supabase")
        assert verifier is not None

        out = verifier("local store", {"placeholder": True})

        # The "swap": the source passed in lands as service; "supabase"
        # lands as source. Do not fix without a behavior-change PR.
        assert out["service"] == "local store"
        assert out["source"] == "supabase"
        assert out["status"] == "passed"
        # Touch the package import to keep ruff from flagging it unused
        # (the import asserts the package is importable end-to-end).
        assert _verifiers_pkg is not None
