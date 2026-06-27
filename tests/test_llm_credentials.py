from __future__ import annotations

import keyring

import config.llm_credentials as llm_credentials
from tests.shared.keyring_backend import MemoryKeyring


def test_resolve_env_credential_prefers_env_over_keyring(monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        llm_credentials.save_llm_api_key("GITLAB_ACCESS_TOKEN", "from-keyring")
        assert llm_credentials.resolve_env_credential("GITLAB_ACCESS_TOKEN") == "from-env"
    finally:
        keyring.set_keyring(previous_backend)


def test_llm_api_key_source_reports_env_keyring_and_none(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        assert llm_credentials.llm_api_key_source("OPENAI_API_KEY") == "none"
        llm_credentials.save_llm_api_key("OPENAI_API_KEY", "from-keyring")
        assert llm_credentials.llm_api_key_source("OPENAI_API_KEY") == "keyring"
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        assert llm_credentials.llm_api_key_source("OPENAI_API_KEY") == "env"
    finally:
        keyring.set_keyring(previous_backend)


def test_llm_credential_record_round_trips_in_keyring(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        llm_credentials.save_llm_credential_record(
            "provider-auth:deepseek",
            {"provider": "deepseek", "source": "keyring", "empty": ""},
        )

        assert llm_credentials.resolve_llm_credential_record("provider-auth:deepseek") == {
            "provider": "deepseek",
            "source": "keyring",
        }

        llm_credentials.delete_llm_credential_record("provider-auth:deepseek")
        assert llm_credentials.resolve_llm_credential_record("provider-auth:deepseek") == {}
    finally:
        keyring.set_keyring(previous_backend)


def test_get_keyring_setup_instructions_for_linux_without_gnome_keyring(monkeypatch) -> None:
    backend_class = type("Keyring", (), {})
    backend_class.__module__ = "keyring.backends.fail"

    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setattr(llm_credentials.platform, "system", lambda: "Linux")
    monkeypatch.setattr(llm_credentials.shutil, "which", lambda _name: None)
    monkeypatch.setattr(llm_credentials.keyring, "get_keyring", lambda: backend_class())

    lines = llm_credentials.get_keyring_setup_instructions("ANTHROPIC_API_KEY")

    assert lines[0] == "Current keyring backend: keyring.backends.fail.Keyring."
    assert "missing the GNOME Keyring daemon" in lines[1]
    assert any(
        "sudo apt update && sudo apt install -y gnome-keyring dbus-user-session" in line
        for line in lines
    )
    assert any("dbus-run-session -- sh" in line for line in lines)


def test_get_keyring_setup_instructions_when_keyring_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")

    lines = llm_credentials.get_keyring_setup_instructions("OPENAI_API_KEY")

    assert lines == (
        "Secure local credential storage is disabled by OPENSRE_DISABLE_KEYRING.",
        "Unset OPENSRE_DISABLE_KEYRING and rerun `opensre onboard` to save OPENAI_API_KEY securely.",
    )
