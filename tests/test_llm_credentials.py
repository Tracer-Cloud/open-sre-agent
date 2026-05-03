from __future__ import annotations

from app import llm_credentials


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
