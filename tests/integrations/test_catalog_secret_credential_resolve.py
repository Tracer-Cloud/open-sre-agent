"""Catalog secret env loaders resolve via env then keyring (PR2)."""

from __future__ import annotations

import keyring
import pytest

import config.llm_credentials as llm_credentials
from integrations.catalog import load_env_integrations
from tests.shared.keyring_backend import MemoryKeyring


@pytest.fixture
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> MemoryKeyring:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    yield backend
    keyring.set_keyring(previous)


def _clear_secret_env(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_grafana_token_loads_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    _clear_secret_env(monkeypatch, "GRAFANA_INSTANCE_URL", "GRAFANA_READ_TOKEN")
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://grafana.example.com")
    llm_credentials.save_keyring_secret("GRAFANA_READ_TOKEN", "glsa_from_keyring")
    records = load_env_integrations()
    grafana = next(r for r in records if r.get("service") == "grafana")
    assert grafana["credentials"]["api_key"] == "glsa_from_keyring"


def test_datadog_keys_env_win_over_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    _clear_secret_env(monkeypatch, "DD_API_KEY", "DD_APP_KEY", "DD_SITE")
    llm_credentials.save_keyring_secret("DD_API_KEY", "dd-keyring")
    llm_credentials.save_keyring_secret("DD_APP_KEY", "dd-app-keyring")
    monkeypatch.setenv("DD_API_KEY", "dd-env")
    monkeypatch.setenv("DD_APP_KEY", "dd-app-env")
    records = load_env_integrations()
    datadog = next(r for r in records if r.get("service") == "datadog")
    assert datadog["credentials"]["api_key"] == "dd-env"
    assert datadog["credentials"]["app_key"] == "dd-app-env"


def test_sentry_auth_token_loads_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    _clear_secret_env(monkeypatch, "SENTRY_ORG_SLUG", "SENTRY_AUTH_TOKEN")
    monkeypatch.setenv("SENTRY_ORG_SLUG", "acme")
    llm_credentials.save_keyring_secret("SENTRY_AUTH_TOKEN", "sentry-from-keyring")
    records = load_env_integrations()
    sentry = next(r for r in records if r.get("service") == "sentry")
    assert sentry["credentials"]["auth_token"] == "sentry-from-keyring"
