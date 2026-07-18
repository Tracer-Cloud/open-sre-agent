"""Package *_from_env helpers resolve secrets via env then keyring."""

from __future__ import annotations

import keyring
import pytest

import config.llm_credentials as llm_credentials
from integrations.airflow.config import airflow_config_from_env
from integrations.gitlab import gitlab_config_from_env
from integrations.jenkins import jenkins_config_from_env
from integrations.posthog.config import posthog_config_from_env
from integrations.sentry import sentry_config_from_env
from integrations.tempo import tempo_config_from_env
from integrations.trello.config import trello_config_from_env
from tests.shared.keyring_backend import MemoryKeyring


@pytest.fixture
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> MemoryKeyring:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    yield backend
    keyring.set_keyring(previous)


def test_tempo_api_key_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.setenv("TEMPO_URL", "http://localhost:3200")
    monkeypatch.delenv("TEMPO_API_KEY", raising=False)
    monkeypatch.delenv("TEMPO_PASSWORD", raising=False)
    llm_credentials.save_keyring_secret("TEMPO_API_KEY", "tempo-keyring")
    config = tempo_config_from_env()
    assert config is not None
    assert config.api_key == "tempo-keyring"


def test_gitlab_token_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    llm_credentials.save_keyring_secret("GITLAB_ACCESS_TOKEN", "glpat-keyring")
    config = gitlab_config_from_env()
    assert config is not None
    assert config.auth_token == "glpat-keyring"


def test_jenkins_token_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.example.com")
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    llm_credentials.save_keyring_secret("JENKINS_API_TOKEN", "jenkins-keyring")
    config = jenkins_config_from_env()
    assert config is not None
    assert config.api_token == "jenkins-keyring"


def test_posthog_personal_key_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.setenv("POSTHOG_PROJECT_ID", "123")
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)
    llm_credentials.save_keyring_secret("POSTHOG_PERSONAL_API_KEY", "phc-keyring")
    config = posthog_config_from_env()
    assert config is not None
    assert config.personal_api_key == "phc-keyring"


def test_airflow_password_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.setenv("AIRFLOW_USERNAME", "admin")
    monkeypatch.delenv("AIRFLOW_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AIRFLOW_PASSWORD", raising=False)
    llm_credentials.save_keyring_secret("AIRFLOW_PASSWORD", "airflow-keyring")
    config = airflow_config_from_env()
    assert config is not None
    assert config.password == "airflow-keyring"


def test_trello_secrets_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    llm_credentials.save_keyring_secret("TRELLO_API_KEY", "trello-key")
    llm_credentials.save_keyring_secret("TRELLO_TOKEN", "trello-token")
    config = trello_config_from_env()
    assert config is not None
    assert config.api_key == "trello-key"
    assert config.token == "trello-token"


def test_sentry_helper_token_from_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    monkeypatch.setenv("SENTRY_ORG_SLUG", "acme")
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    llm_credentials.save_keyring_secret("SENTRY_AUTH_TOKEN", "sentry-helper-keyring")
    config = sentry_config_from_env()
    assert config is not None
    assert config.auth_token == "sentry-helper-keyring"
