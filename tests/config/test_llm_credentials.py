from __future__ import annotations

import subprocess

import google.auth
import keyring
from google.auth.exceptions import DefaultCredentialsError

import config.llm_credentials as llm_credentials
import config.llm_keyring as llm_keyring
from config.llm_auth.credentials import (
    has_llm_api_key,
    llm_api_key_source,
    resolve_for_request,
    status,
)
from config.llm_auth.records import save_provider_auth_record
from tests.shared.keyring_backend import MemoryKeyring


class _MacOSKeyringBackend:
    pass


_MacOSKeyringBackend.__module__ = "keyring.backends.macOS"


def _security_tool_path(name: str) -> str:
    return f"/usr/bin/{name}"


def _darwin_platform() -> str:
    return "Darwin"


def test_status_vertex_ai_configured_when_adc_resolves(monkeypatch) -> None:
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "europe-west1")
    monkeypatch.setattr(google.auth, "default", lambda: (object(), "my-gcp-project"))

    result = status("vertex-ai")

    assert result.configured is True
    assert result.source == "ambient"
    assert "my-gcp-project" in result.detail


def test_status_vertex_ai_not_configured_when_adc_missing_despite_project_env(
    monkeypatch,
) -> None:
    """VERTEX_AI_PROJECT being set is not proof that ADC actually resolves."""
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")

    def _raise_no_adc() -> tuple[object, str | None]:
        raise DefaultCredentialsError("no ADC found")

    monkeypatch.setattr(google.auth, "default", _raise_no_adc)

    result = status("vertex-ai")

    assert result.configured is False
    assert result.source == "none"


def test_status_vertex_ai_configured_via_metadata_without_project_env(monkeypatch) -> None:
    """ADC discovered through GCE/GKE metadata counts even with no project env set."""
    monkeypatch.delenv("VERTEX_AI_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_AI_LOCATION", raising=False)
    monkeypatch.setattr(google.auth, "default", lambda: (object(), "discovered-project"))

    result = status("vertex-ai")

    assert result.configured is True
    assert result.source == "ambient"
    assert "discovered-project" in result.detail


def test_status_vertex_ai_not_configured_when_adc_resolves_without_project(
    monkeypatch,
) -> None:
    """ADC succeeding is not enough — a request still needs a resolvable project.

    Regression test: this used to fall back to a display-only "auto-discovered"
    placeholder and report configured=True, even though request routing has no
    project to send and the subsequent LiteLLM call would fail.
    """
    monkeypatch.delenv("VERTEX_AI_PROJECT", raising=False)
    monkeypatch.setattr(google.auth, "default", lambda: (object(), None))

    result = status("vertex-ai")

    assert result.configured is False
    assert result.source == "none"
    assert "VERTEX_AI_PROJECT" in result.detail


def test_status_bedrock_ignores_vertex_project_env(monkeypatch) -> None:
    """The ambient status branch must not cross-check unrelated providers' env vars."""
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    result = status("bedrock")

    assert result.configured is False
    assert "AWS_REGION" in result.detail


def test_resolve_env_credential_prefers_env_over_keyring(monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        llm_credentials.save_keyring_secret("GITLAB_ACCESS_TOKEN", "from-keyring")
        assert llm_credentials.resolve_env_credential("GITLAB_ACCESS_TOKEN") == "from-env"
    finally:
        keyring.set_keyring(previous_backend)


def test_resolve_keyring_secret_reads_keyring_only(monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        llm_credentials.save_keyring_secret("GITLAB_ACCESS_TOKEN", "from-keyring")
        assert llm_credentials.resolve_keyring_secret("GITLAB_ACCESS_TOKEN") == "from-keyring"
    finally:
        keyring.set_keyring(previous_backend)


def test_resolve_keyring_secret_swallows_backend_runtime_error(monkeypatch) -> None:
    """SecretService can raise bare RuntimeError when D-Bus is unset."""
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    # Use a name unlikely to be present in CI secrets / ambient env.
    env_var = "OPENSRE_TEST_MISSING_KEYRING_SECRET"
    monkeypatch.delenv(env_var, raising=False)

    def _boom(_service: str, _username: str) -> str:
        raise RuntimeError("Unable to initialize SecretService: DBUS unset")

    monkeypatch.setattr(llm_keyring.keyring, "get_password", _boom)
    assert llm_credentials.resolve_keyring_secret(env_var) == ""
    assert llm_credentials.resolve_env_credential(env_var) == ""


def test_unmanaged_llm_api_key_source_reports_env_keyring_and_none(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("EXPERIMENTAL_API_KEY", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        assert llm_api_key_source("EXPERIMENTAL_API_KEY") == "none"
        llm_credentials.save_keyring_secret("EXPERIMENTAL_API_KEY", "from-keyring")
        assert llm_api_key_source("EXPERIMENTAL_API_KEY") == "keyring"
        monkeypatch.setenv("EXPERIMENTAL_API_KEY", "from-env")
        assert llm_api_key_source("EXPERIMENTAL_API_KEY") == "env"
    finally:
        keyring.set_keyring(previous_backend)


def test_managed_llm_api_key_source_uses_metadata_without_reading_secret(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(llm_keyring.platform, "system", _darwin_platform)
    monkeypatch.setattr(llm_keyring.shutil, "which", _security_tool_path)
    monkeypatch.setattr(llm_keyring.keyring, "get_keyring", _MacOSKeyringBackend)

    def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            "opensre.llm",
            "-a",
            "OPENAI_API_KEY",
        ]
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0)

    def _get_password(_service: str, _username: str) -> str:
        raise AssertionError("metadata source check must not read the keychain secret")

    monkeypatch.setattr(llm_keyring.subprocess, "run", _run)
    monkeypatch.setattr(llm_keyring.keyring, "get_password", _get_password)
    save_provider_auth_record(
        provider="openai",
        auth_name="openai",
        kind="api_key",
        source="keyring",
        detail="OPENAI_API_KEY stored in the system keychain.",
        env_var="OPENAI_API_KEY",
    )

    assert llm_api_key_source("OPENAI_API_KEY") == "metadata"
    assert has_llm_api_key("OPENAI_API_KEY") is True


def test_managed_missing_metadata_reports_none_without_reading_secret(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(llm_keyring.platform, "system", _darwin_platform)
    monkeypatch.setattr(llm_keyring.shutil, "which", _security_tool_path)
    monkeypatch.setattr(llm_keyring.keyring, "get_keyring", _MacOSKeyringBackend)

    def _run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 44)

    def _get_password(_service: str, _username: str) -> str:
        raise AssertionError("metadata source check must not read the keychain secret")

    monkeypatch.setattr(llm_keyring.subprocess, "run", _run)
    monkeypatch.setattr(llm_keyring.keyring, "get_password", _get_password)

    assert llm_api_key_source("OPENAI_API_KEY") == "none"
    assert has_llm_api_key("OPENAI_API_KEY") is False


def test_request_resolution_marks_deleted_keychain_metadata_stale(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    save_provider_auth_record(
        provider="deepseek",
        auth_name="deepseek",
        kind="api_key",
        source="keyring",
        detail="DEEPSEEK_API_KEY stored in the system keychain.",
        env_var="DEEPSEEK_API_KEY",
    )

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        before = status("deepseek")
        resolution = resolve_for_request("deepseek")
        after = status("deepseek")
    finally:
        keyring.set_keyring(previous_backend)

    assert before.configured is True
    assert before.stale is False
    assert resolution.ok is False
    assert after.configured is True
    assert after.stale is True
    assert after.verified is False
    assert "Missing credential" in after.detail


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
    monkeypatch.setattr(llm_keyring.platform, "system", lambda: "Linux")
    monkeypatch.setattr(llm_keyring.shutil, "which", lambda _name: None)
    monkeypatch.setattr(llm_keyring.keyring, "get_keyring", lambda: backend_class())

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
