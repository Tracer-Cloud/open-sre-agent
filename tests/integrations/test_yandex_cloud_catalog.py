"""Catalog wiring for the Yandex Cloud integration.

Covers the path a credential actually travels: a stored record or a ``YC_*``
environment is classified into a typed config, survives effective resolution,
and reaches a tool through ``sources``. Asserting on the config model alone
would pass while any one of those steps dropped the record.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.yandex_cloud import (
    YC_ENDPOINT_OVERRIDES_ENV,
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_USE_METADATA_ENV,
)
from core.tool import availability_view
from integrations.catalog import (
    classify_integrations,
    load_env_integrations,
    resolve_effective_integrations,
)
from integrations.yandex_cloud import classify
from integrations.yandex_cloud.availability import (
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig

FOLDER = "b1gtestfolder"

STORE_RECORD = {
    "service": "yandex_cloud",
    "id": "yc-1",
    "status": "active",
    "credentials": {"folder_id": FOLDER, "iam_token": "t1.test-token"},
}


class TestClassify:
    def test_returns_typed_config_with_required_fields(self) -> None:
        cfg, key = classify(
            {"folder_id": FOLDER, "cloud_id": "b1cloud", "iam_token": "t1.token"},
            record_id="rec-1",
        )

        assert key == "yandex_cloud"
        assert isinstance(cfg, YandexCloudIntegrationConfig)
        assert cfg.folder_id == FOLDER
        assert cfg.cloud_id == "b1cloud"
        assert cfg.integration_id == "rec-1"

    def test_skips_a_record_with_no_credential(self) -> None:
        """A folder alone cannot authenticate, so the catalog must skip it.

        Publishing it would leave an integration that reads as configured and
        fails on the first call.
        """
        cfg, key = classify({"folder_id": FOLDER}, record_id="rec-1")

        assert cfg is None
        assert key is None

    def test_skips_a_record_with_no_folder(self) -> None:
        """Every Yandex Cloud read is folder-scoped; the API rejects the rest."""
        cfg, key = classify({"iam_token": "t1.token"}, record_id="rec-1")

        assert cfg is None
        assert key is None

    def test_metadata_auth_needs_no_folder(self) -> None:
        """An instance knows which folder it runs in, so it need not be told."""
        cfg, key = classify({"use_metadata": True}, record_id="rec-1")

        assert key == "yandex_cloud"
        assert cfg is not None
        assert cfg.use_metadata is True

    def test_unused_credentials_stored_as_null_are_accepted(self) -> None:
        """``integrations setup`` writes unset fields as null, not as absent.

        The config model is strict, so without normalization a record saved by
        the wizard would fail validation and read as "saved but unusable".
        """
        cfg, key = classify(
            {
                "folder_id": FOLDER,
                "iam_token": "t1.token",
                "sa_key_file": None,
                "sa_key": None,
                "oauth_token": None,
                "cloud_id": None,
            },
            record_id="rec-1",
        )

        assert key == "yandex_cloud"
        assert cfg is not None
        assert cfg.sa_key_file == ""


class TestCatalogEndToEnd:
    """Store record -> classified -> availability view -> tool credentials."""

    def test_a_stored_record_reaches_a_tool(self) -> None:
        resolved = classify_integrations([STORE_RECORD])
        view = availability_view(resolved)

        assert yc_available_or_backend(view) is True
        assert yc_credentials(view)["folder_id"] == FOLDER

    def test_a_stored_record_survives_effective_resolution(self) -> None:
        effective = resolve_effective_integrations(
            store_integrations=[STORE_RECORD], env_integrations=[]
        )

        assert "yandex_cloud" in effective

    def test_a_synthetic_backend_makes_tools_available_without_credentials(self) -> None:
        """Synthetic tests attach a fixture instead of reaching the cloud."""
        view = {"yandex_cloud": {"_backend": object()}}

        assert yc_available_or_backend(view) is True

    def test_nothing_configured_means_unavailable(self) -> None:
        assert yc_available_or_backend({}) is False


class TestEnvironmentLoading:
    def test_folder_and_token_are_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
        monkeypatch.setenv(YC_IAM_TOKEN_ENV, "t1.env-token")

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert len(records) == 1
        assert records[0]["credentials"]["folder_id"] == FOLDER

    def test_a_folder_without_a_credential_is_not_loaded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
        monkeypatch.delenv(YC_IAM_TOKEN_ENV, raising=False)
        monkeypatch.delenv(YC_SA_KEY_FILE_ENV, raising=False)
        # setup writes this to .env, so on any machine with the integration
        # configured it leaks in and the folder does have a credential after
        # all - green in CI, red for anyone who actually connected it.
        monkeypatch.delenv(YC_USE_METADATA_ENV, raising=False)

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert records == []

    def test_metadata_alone_is_enough_on_an_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(YC_FOLDER_ID_ENV, raising=False)
        monkeypatch.setenv(YC_USE_METADATA_ENV, "true")

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert len(records) == 1
        assert records[0]["credentials"]["use_metadata"] is True

    def test_nothing_set_loads_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (YC_FOLDER_ID_ENV, YC_IAM_TOKEN_ENV, YC_USE_METADATA_ENV):
            monkeypatch.delenv(name, raising=False)

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert records == []


class TestRegistryWiring:
    def test_setup_and_verify_list_the_service(self) -> None:
        from integrations.registry import SUPPORTED_SETUP_SERVICES, SUPPORTED_VERIFY_SERVICES

        assert "yandex_cloud" in SUPPORTED_SETUP_SERVICES
        assert "yandex_cloud" in SUPPORTED_VERIFY_SERVICES

    def test_setup_has_a_handler(self) -> None:
        """A spec with a setup order but no handler is accepted by Click and
        then fails to dispatch."""
        from integrations.cli import _HANDLERS

        assert "yandex_cloud" in _HANDLERS

    def test_a_verifier_is_registered(self) -> None:
        from integrations.verification import get_verifier

        assert get_verifier("yandex_cloud") is not None

    def test_the_vendor_cli_name_resolves(self) -> None:
        """``yc`` is what the vendor's own CLI is called, so people type it."""
        from integrations.registry import service_key

        assert service_key("yc") == "yandex_cloud"
        assert service_key("yandex") == "yandex_cloud"


class TestPresencePath:
    """The fast, pre-prompt presence check must agree with the full loader.

    `load_env_integration_services` runs before the first prompt and drives the
    welcome banner, the REPL and `health`. If it omits a service the full loader
    accepts, those surfaces contradict `verify` and effective resolution.
    """

    def test_a_configured_folder_and_token_show(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
        monkeypatch.setenv(YC_IAM_TOKEN_ENV, "t1.token")

        from integrations.catalog import load_env_integration_services

        assert "yandex_cloud" in load_env_integration_services()

    def test_metadata_alone_shows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(YC_FOLDER_ID_ENV, raising=False)
        monkeypatch.setenv(YC_USE_METADATA_ENV, "true")

        from integrations.catalog import load_env_integration_services

        assert "yandex_cloud" in load_env_integration_services()

    def test_a_bare_folder_does_not_show(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A folder with no credential is not a configured integration."""
        monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
        for name in (YC_IAM_TOKEN_ENV, YC_SA_KEY_FILE_ENV, YC_USE_METADATA_ENV):
            monkeypatch.delenv(name, raising=False)

        from integrations.catalog import load_env_integration_services

        assert "yandex_cloud" not in load_env_integration_services()


class _FakeTokenResponse:
    """Stands in for the metadata service's token reply."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class TestMetadataTokenTtl:
    """A metadata token must never be cached past its stated expiry."""

    def _patch_metadata_response(
        self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
    ) -> None:
        from integrations.yandex_cloud import metadata

        def _get(*_args: Any, **_kwargs: Any) -> _FakeTokenResponse:
            return _FakeTokenResponse(payload)

        monkeypatch.setattr(metadata.httpx, "get", _get)

    def test_a_near_expiry_token_gets_a_short_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Folding the safety margin into `or fallback` cached a dying token for
        the full fallback window; the two cases must stay separate."""
        from integrations.yandex_cloud import metadata

        self._patch_metadata_response(monkeypatch, {"access_token": "t1.token", "expires_in": 100})

        minted = metadata.fetch_token()

        assert minted is not None
        # 100s left minus the 300s safety margin, floored at zero - not 3000.
        assert minted.ttl_seconds == 0.0

    def test_a_missing_expiry_uses_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from integrations.yandex_cloud import metadata

        self._patch_metadata_response(monkeypatch, {"access_token": "t1.token"})

        minted = metadata.fetch_token()

        assert minted is not None
        assert minted.ttl_seconds == metadata._FALLBACK_TTL_SECONDS


class TestClientCache:
    """A cached client must never outlive the credential it was built for."""

    def test_a_rotated_credential_gets_a_fresh_client(self) -> None:
        from integrations.yandex_cloud.availability import (
            client_from_params,
            reset_client_cache,
        )

        reset_client_cache()
        old = client_from_params({"folder_id": "b1g", "iam_token": "OLD"})
        new = client_from_params({"folder_id": "b1g", "iam_token": "NEW"})

        assert old is not new
        assert new is not None
        assert new.config.iam_token == "NEW"

    def test_the_same_credential_reuses_the_client(self) -> None:
        from integrations.yandex_cloud.availability import (
            client_from_params,
            reset_client_cache,
        )

        reset_client_cache()
        first = client_from_params({"folder_id": "b1g", "iam_token": "t1.token"})
        again = client_from_params({"folder_id": "b1g", "iam_token": "t1.token"})

        assert first is again


class TestEndpointRegistryBackoff:
    """A failed registry fetch must back off, not retry on every call."""

    def test_a_failed_fetch_is_not_retried_before_the_cache_period(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import integrations.yandex_cloud.endpoints as endpoints

        endpoints.reset_endpoint_cache()
        attempts = {"n": 0}

        def _fail(*_args: Any, **_kwargs: Any) -> Any:
            attempts["n"] += 1
            raise RuntimeError("registry unreachable")

        monkeypatch.setattr(endpoints.httpx, "get", _fail)
        # A freshly booted host reports a small time.monotonic(); the cache must
        # still fetch the first time rather than mistake "never fetched" for
        # "fetched recently" and skip it. (This is what failed in CI.)
        monkeypatch.setattr(endpoints.time, "monotonic", lambda: 50.0)

        for _ in range(3):
            resolved = endpoints.known_endpoints()

        assert attempts["n"] == 1
        # The snapshot still answers, so resolution does not regress.
        assert "compute" in resolved


class TestSetupMetadataFlag:
    """``use_metadata`` is persisted true only when it is the actual credential."""

    def test_a_key_based_setup_does_not_persist_the_metadata_flag(self) -> None:
        from integrations.yandex_cloud.setup import _resolve_metadata_flag

        resolved = _resolve_metadata_flag(
            {"folder_id": "b1g", "sa_key_file": "/tmp/key.json", "use_metadata": "true"}
        )

        assert resolved.credentials["use_metadata"] is None

    def test_the_metadata_mode_keeps_the_flag(self) -> None:
        from integrations.yandex_cloud.setup import _resolve_metadata_flag

        resolved = _resolve_metadata_flag({"folder_id": "b1g", "use_metadata": "true"})

        assert resolved.credentials["use_metadata"] == "true"


class TestObjectStorageResolvesToItsControlPlane:
    """ "storage" reaches the control plane, and an operator can still redirect it.

    The registry gives that name to the S3 data plane, which serves objects and
    answers no control-plane read; the control plane is registered as
    "storage-api".
    """

    def test_storage_reads_go_to_the_control_plane(self) -> None:
        from integrations.yandex_cloud.endpoints import resolve_endpoint

        assert resolve_endpoint("storage", refresh=False) == "storage.api.cloud.yandex.net"

    def test_the_alias_survives_a_registry_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refresh brings back Yandex's own mapping; the alias must outlive it."""
        from integrations.yandex_cloud import endpoints

        monkeypatch.setattr(
            endpoints, "_fetch_endpoints", lambda: {"storage": "storage.yandexcloud.net"}
        )
        endpoints.reset_endpoint_cache()

        assert endpoints.resolve_endpoint("storage") == "storage.api.cloud.yandex.net"

    def test_an_override_on_the_caller_visible_name_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator overrides the name they call, not the one the registry uses."""
        from integrations.yandex_cloud import endpoints

        monkeypatch.setenv(YC_ENDPOINT_OVERRIDES_ENV, '{"storage": "storage.internal"}')
        endpoints.reset_endpoint_cache()

        assert endpoints.resolve_endpoint("storage", refresh=False) == "storage.internal"

    def test_an_override_on_the_registry_name_also_lands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Either name is a fair thing to override, so both have to take effect."""
        from integrations.yandex_cloud import endpoints

        monkeypatch.setenv(YC_ENDPOINT_OVERRIDES_ENV, '{"storage-api": "storage.internal"}')
        endpoints.reset_endpoint_cache()

        assert endpoints.resolve_endpoint("storage", refresh=False) == "storage.internal"
        assert endpoints.resolve_endpoint("storage-api", refresh=False) == "storage.internal"
