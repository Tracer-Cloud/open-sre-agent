from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import get_store_path


def test_billing_env_var_names_are_the_infra_contract() -> None:
    """Pin the credit-metering env-var names to the exact strings the org-silo
    infra injects — a rename here silently disables metering in production."""
    # Arrange / Act
    from config.constants import billing

    # Assert
    assert billing.WEBAPP_URL_ENV == "OPENSRE_WEBAPP_URL"
    assert billing.MACHINE_SECRET_ENV == "CLERK_MACHINE_SECRET_KEY"
    assert billing.USAGE_SECRET_ENV == "AGENT_USAGE_SECRET"
    assert billing.ORGANIZATION_ID_ENV == "OPENSRE_ORGANIZATION_ID"
    assert billing.CREDITS_HTTP_TIMEOUT_SECONDS == 5.0


def test_constants_module_stays_a_leaf() -> None:
    """``config`` sits at the bottom layer, so the billing constants must not
    reach up into another package — that would form an import cycle."""
    # Arrange / Act
    from pathlib import Path as _Path

    source = _Path("config/constants/billing.py").read_text(encoding="utf-8")

    # Assert: no upward import of a sibling top-level package.
    for package in ("integrations", "gateway", "core", "platform", "tools", "surfaces"):
        assert f"import {package}" not in source
        assert f"from {package}" not in source


def test_llm_env_var_names_are_the_infra_contract() -> None:
    """Pin the Azure OpenAI connection env-var names to their exact strings."""
    # Arrange / Act
    from config.constants import llm

    # Assert
    assert llm.AZURE_OPENAI_BASE_URL_ENV == "AZURE_OPENAI_BASE_URL"
    assert llm.AZURE_OPENAI_API_VERSION_ENV == "AZURE_OPENAI_API_VERSION"
    assert llm.AZURE_OPENAI_API_KEY_ENV == "AZURE_OPENAI_API_KEY"


def test_provider_catalog_and_wizard_share_the_same_azure_constants() -> None:
    """The Azure spec + wizard option must reference the one set of constants,
    not re-typed literals, so they cannot drift apart."""
    # Arrange
    from config.constants import llm
    from config.llm_auth.provider_catalog import require_provider_spec
    from surfaces.cli.wizard.config import SUPPORTED_PROVIDERS

    spec = require_provider_spec("azure-openai")
    (option,) = [opt for opt in SUPPORTED_PROVIDERS if opt.value == "azure-openai"]

    # Act / Assert: both sides equal the centralized constants.
    assert spec.api_key_env == option.api_key_env == llm.AZURE_OPENAI_API_KEY_ENV
    assert spec.endpoint_env == option.endpoint_env == llm.AZURE_OPENAI_BASE_URL_ENV
    assert spec.api_version_env == option.api_version_env == llm.AZURE_OPENAI_API_VERSION_ENV


def test_get_store_path_honors_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom-dir" / "opensre.json"
    monkeypatch.setenv("OPENSRE_WIZARD_STORE_PATH", str(override))

    assert get_store_path() == override


def test_get_store_path_defaults_away_from_real_home_during_tests(tmp_path: Path) -> None:
    """Regression guard for #3721.

    The root ``tests/conftest.py`` autouse fixture ``_isolate_opensre_home_files``
    sets ``OPENSRE_WIZARD_STORE_PATH`` for every test by default, specifically so
    a test that forgets to monkeypatch ``get_store_path`` can never fall through
    to the developer's real ``~/.opensre/opensre.json``. This test intentionally
    does *not* patch anything itself, to prove that default is in effect.
    """
    resolved = get_store_path()

    assert resolved != Path.home() / ".opensre" / "opensre.json"
    # Same tmp_path instance the autouse fixture pointed OPENSRE_WIZARD_STORE_PATH at.
    assert resolved == tmp_path / "opensre.json"
