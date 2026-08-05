from __future__ import annotations

import dataclasses
import json
import os
from unittest.mock import MagicMock

import pytest

import integrations.setup_flow as _setup_flow
from config.secrets.store import SecretSaveResult
from integrations.llm_cli.codex_oauth import CodexOAuthResult
from surfaces.cli.wizard import _ui, azure_openai, flow, llm_credential
from surfaces.cli.wizard import store as wizard_store
from surfaces.cli.wizard.configurators import chat_notifications as _chat_notifications_configurator
from surfaces.cli.wizard.configurators import dagster as _dagster_configurator
from surfaces.cli.wizard.configurators import github as _github_configurator
from surfaces.cli.wizard.configurators import gitlab as _gitlab_configurator
from surfaces.cli.wizard.configurators import observability as _observability_configurator
from surfaces.cli.wizard.env_sync import sync_provider_env
from surfaces.cli.wizard.probes import ProbeResult
from surfaces.cli.wizard.validation import ValidationResult
from tests.integrations.llm_cli.testing_helpers import write_fake_runnable_cli_bin

# Persisting a secret reports which storage tier accepted it, so stubs have to
# answer that question too — a stub returning ``None`` would make every wizard
# run look like it had fallen back to on-disk storage.
_KEYRING_SAVE = SecretSaveResult("keyring", "stored in the system keychain.")


def _stub_save(*_args: object, **_kwargs: object) -> SecretSaveResult:
    return _KEYRING_SAVE


def _stub_save_recording(saved: list, provider: str, value: str) -> SecretSaveResult:
    saved.append((provider, value))
    return _KEYRING_SAVE


def _stub_telegram_setup(monkeypatch: pytest.MonkeyPatch, verify) -> None:
    """Swap the Telegram spec's network hooks, keeping its real field definitions.

    ``_configure_telegram`` runs the shared setup flow, so the seam is the spec
    rather than a validator function: *verify* stands in for the Bot API
    ``getMe`` probe, and the chat-id resolution is short-circuited to echo back
    whatever the user typed.
    """
    import dataclasses

    monkeypatch.setattr(
        _chat_notifications_configurator,
        "TELEGRAM_SETUP",
        dataclasses.replace(
            _chat_notifications_configurator.TELEGRAM_SETUP,
            verify=verify,
            resolve=lambda credentials: _setup_flow.ResolvedCredentials(credentials=credentials),
        ),
    )


def _stub_dagster_setup(monkeypatch: pytest.MonkeyPatch, verify) -> None:
    """Swap the Dagster spec's verifier, keeping its real field definitions."""
    monkeypatch.setattr(
        _dagster_configurator,
        "DAGSTER_SETUP",
        dataclasses.replace(_dagster_configurator.DAGSTER_SETUP, verify=verify),
    )


@pytest.fixture(autouse=True)
def _stub_managed_llm_secret_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wizard flow tests should not touch the developer's real keychain."""
    monkeypatch.setattr(_ui, "save_api_key", _stub_save)


@pytest.fixture(autouse=True)
def _stub_llm_credential_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wizard flow tests must never hit live provider endpoints."""
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="stubbed"),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _isolate_integration_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Wizard reads existing config via ``integrations.store``; point it at an
    empty temp store so tests never read the developer's real integrations
    (e.g. a live Slack bot token) as pre-existing defaults."""
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")


@pytest.fixture(autouse=True)
def _skip_loop_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flow, "_seed_onboarding_loops", lambda: 0)


def test_run_wizard_advanced_remote_falls_back_to_local(monkeypatch, tmp_path, capsys) -> None:
    # advanced -> falls back to local -> change provider? Yes -> pick anthropic -> skip integrations
    select_responses = iter(
        ["advanced", "remote", "anthropic", "api_key", "claude-opus-4-7", "skip"]
    )
    confirm_responses = iter([True, True])  # "use local instead?" and "Change provider?"

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(confirm_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    saved: dict[str, object] = {}
    saved_llm_keys: list[tuple[str, str]] = []
    seeded_loops: list[bool] = []

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow, "probe_remote_target", lambda: ProbeResult("remote", True, "remote ok")
    )

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "_seed_onboarding_loops", lambda: seeded_loops.append(True) or 3)
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "advanced"
    assert saved["provider"] == "anthropic"
    assert "api_key" not in saved
    assert saved_llm_keys == [("anthropic", "secret-key")]
    assert seeded_loops == [True]

    output = capsys.readouterr().out
    assert "Summary" in output
    assert "4/4" in output
    assert "next" in output
    assert "Done." in output
    assert output.index("Summary") < output.index("Done.")


def test_prompt_value_can_signal_wizard_back(monkeypatch) -> None:
    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = None
        return m

    monkeypatch.setattr(_ui.questionary, "password", _mock_password)

    with pytest.raises(_ui.WizardBack):
        _ui._prompt_value("OpenAI API key", secret=True, back_on_cancel=True)


def test_run_wizard_no_saved_provider_shows_selection(monkeypatch, tmp_path) -> None:
    """With no saved config the provider list is shown immediately (no confirm prompt)."""
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "skip"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    exit_code = flow.run_wizard()
    assert exit_code == 0


def test_run_wizard_shows_keyring_fix_steps_when_secure_storage_is_unavailable(
    monkeypatch, tmp_path, capsys
) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7"])

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        # ESCAPE at the keychain recovery menu -> "Setup cancelled." -> exit 1.
        m.ask.return_value = None if "What next?" in prompt else next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Secure local credential storage is unavailable on this machine.")
        ),
    )
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: (
            "Current keyring backend: keyring.backends.fail.Keyring.",
            "Install it first: sudo apt update && sudo apt install -y gnome-keyring dbus-user-session",
            "Start a D-Bus shell: dbus-run-session -- sh",
        ),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "OpenSRE could not save your API key to secure local storage." in output
    assert "Install it first: sudo apt update && sudo apt install -y gnome-keyring" in output
    assert "dbus-user-session" in output
    assert "Start a D-Bus shell: dbus-run-session -- sh" in output


def test_run_wizard_configures_optional_integrations(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "grafana"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    password_responses = iter(
        [
            "llm-secret",
            "grafana-token",
        ]
    )
    # endpoint, verify_ssl (Enter → default "true"), ca_bundle (blank)
    text_responses = iter(["https://grafana.example.com", "", ""])

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "GRAFANA_SETUP",
        dataclasses.replace(
            _observability_configurator.GRAFANA_SETUP,
            verify=lambda _source, _config: {"status": "passed", "detail": "Grafana ok"},
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow, "sync_env_secret", lambda key, value: synced_env_secrets.append((key, value))
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "grafana",
            {
                "credentials": {
                    "endpoint": "https://grafana.example.com",
                    "api_key": "grafana-token",
                    "verify_ssl": "true",
                    "ca_bundle": None,
                }
            },
        )
    ]
    assert synced_env_secrets == [("GRAFANA_READ_TOKEN", "grafana-token")]
    assert synced_env_values == [
        {
            "GRAFANA_INSTANCE_URL": "https://grafana.example.com",
            "GRAFANA_VERIFY_SSL": "true",
            "GRAFANA_CA_BUNDLE": "",
        },
    ]
    output = capsys.readouterr().out
    assert "Grafana" in output


def test_run_wizard_configures_honeycomb(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "honeycomb"])
    password_responses = iter(["llm-secret", "hny_test"])
    text_responses = iter(["prod-api", "https://api.honeycomb.io"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "HONEYCOMB_SETUP",
        dataclasses.replace(
            _observability_configurator.HONEYCOMB_SETUP,
            verify=lambda _source, _config: {"status": "passed", "detail": "Honeycomb ok"},
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow, "sync_env_secret", lambda key, value: synced_env_secrets.append((key, value))
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "honeycomb",
            {
                "credentials": {
                    "api_key": "hny_test",
                    "dataset": "prod-api",
                    "base_url": "https://api.honeycomb.io",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "HONEYCOMB_DATASET": "prod-api",
            "HONEYCOMB_API_URL": "https://api.honeycomb.io",
        }
    ]
    # The wizard previously wrote the dataset and URL but dropped the key entirely.
    assert synced_env_secrets == [("HONEYCOMB_API_KEY", "hny_test")]


def test_run_wizard_configures_coralogix(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "coralogix"])
    password_responses = iter(["llm-secret", "cx_test"])
    text_responses = iter(
        [
            "https://api.coralogix.com",
            "payments",
            "worker",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "CORALOGIX_SETUP",
        dataclasses.replace(
            _observability_configurator.CORALOGIX_SETUP,
            verify=lambda _source, _config: {"status": "passed", "detail": "Coralogix ok"},
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow, "sync_env_secret", lambda key, value: synced_env_secrets.append((key, value))
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "coralogix",
            {
                "credentials": {
                    "api_key": "cx_test",
                    "base_url": "https://api.coralogix.com",
                    "application_name": "payments",
                    "subsystem_name": "worker",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "CORALOGIX_API_URL": "https://api.coralogix.com",
            "CORALOGIX_APPLICATION_NAME": "payments",
            "CORALOGIX_SUBSYSTEM_NAME": "worker",
        }
    ]
    # The wizard previously wrote the URL and filters but dropped the key entirely.
    assert synced_env_secrets == [("CORALOGIX_API_KEY", "cx_test")]


def test_run_wizard_configures_dagster(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "dagster"])
    password_responses = iter(["llm-secret", "dag_test_token"])
    text_responses = iter(["http://localhost:3000"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    _stub_dagster_setup(
        monkeypatch, lambda _source, _config: {"status": "passed", "detail": "Dagster ok"}
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow,
        "sync_env_secret",
        lambda key, value: synced_secrets.append((key, value)),
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "dagster",
            {
                "credentials": {
                    "endpoint": "http://localhost:3000",
                    "api_token": "dag_test_token",
                }
            },
        )
    ]
    assert synced_env_values == [{"DAGSTER_ENDPOINT": "http://localhost:3000"}]
    assert synced_secrets == [("DAGSTER_API_TOKEN", "dag_test_token")]


def test_run_wizard_configures_dagster_oss_skips_secret(monkeypatch, tmp_path) -> None:
    """OSS path: empty api_token must not call sync_env_secret."""
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "dagster"])
    password_responses = iter(["llm-secret", ""])
    text_responses = iter(["http://localhost:3000"])
    synced_env_values: list[dict[str, str]] = []
    synced_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    _stub_dagster_setup(
        monkeypatch, lambda _source, _config: {"status": "passed", "detail": "Dagster ok"}
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)
    monkeypatch.setattr(
        _setup_flow,
        "sync_env_values",
        lambda values, **_kwargs: synced_env_values.append(values) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(
        _setup_flow,
        "sync_env_secret",
        lambda key, value: synced_secrets.append((key, value)),
    )
    monkeypatch.setattr(_setup_flow, "upsert_integration", lambda *_args, **_kwargs: None)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert synced_env_values == [{"DAGSTER_ENDPOINT": "http://localhost:3000"}]
    # OSS path: token is blank, but apply_setup still mirrors it through so a
    # previously-set secret is cleared rather than left stale.
    assert synced_secrets == [("DAGSTER_API_TOKEN", "")]


def test_run_wizard_configures_slack_persists_webhook(monkeypatch, tmp_path) -> None:
    """Regression: the wizard must upsert the Slack webhook to the store.

    Previously `_configure_slack` validated the webhook and reported "Slack" in
    the success summary but never called `upsert_integration`, silently
    discarding the webhook (the catalog reads Slack store-first, so nothing was
    readable afterwards). The webhook is a secret, so it belongs in the store,
    not `.env` — `sync_env_values` is called with an empty mapping.
    """
    # Pick the "webhook" mode: only the webhook URL is prompted; the socket
    # tokens are cleared, not asked.
    select_responses = iter(
        ["quickstart", "anthropic", "api_key", "claude-opus-4-7", "slack", "webhook"]
    )
    password_responses = iter(["llm-secret", "https://hooks.slack.com/services/T0/B0/XXXXX"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = ""
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _chat_notifications_configurator,
        "SLACK_SETUP",
        dataclasses.replace(
            _chat_notifications_configurator.SLACK_SETUP,
            verify=lambda _source, _config: {"status": "passed", "detail": "Slack ok"},
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow, "sync_env_secret", lambda key, value: synced_env_secrets.append((key, value))
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "slack",
            {
                "credentials": {
                    "webhook_url": "https://hooks.slack.com/services/T0/B0/XXXXX",
                    "bot_token": None,
                    "app_token": None,
                }
            },
        )
    ]
    # Webhook is store-only; blank Socket Mode tokens still clear keyring slots.
    assert synced_env_values == [{}]
    assert synced_env_secrets == [("SLACK_BOT_TOKEN", ""), ("SLACK_APP_TOKEN", "")]


def test_run_wizard_dagster_retries_on_validation_failure(monkeypatch, tmp_path) -> None:
    """Two consecutive Dagster validation failures, then success.

    Proves the retry loop recovers from N consecutive failures (not just one),
    and that only the final successful attempt reaches the persistence layer.
    """
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "dagster"])
    # Two wrong tokens, then the correct one.
    password_responses = iter(["llm-secret", "bad_token_1", "bad_token_2", "dag_good"])
    # endpoint is prompted on each retry (three attempts total).
    text_responses = iter(
        ["http://localhost:3000", "http://localhost:3000", "http://localhost:3000"]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_secrets: list[tuple[str, str]] = []
    validation_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _validate_dagster(_source: str, _config: dict) -> dict[str, str]:
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count < 3:
            return {"status": "failed", "detail": "Dagster GraphQL probe failed: HTTP 401"}
        return {"status": "passed", "detail": "Dagster ok"}

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    _stub_dagster_setup(monkeypatch, _validate_dagster)
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow,
        "sync_env_secret",
        lambda key, value: synced_secrets.append((key, value)),
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert validation_call_count == 3  # two failures, then success
    # Only the successful attempt should reach the persistence layer:
    assert saved_integrations == [
        (
            "dagster",
            {
                "credentials": {
                    "endpoint": "http://localhost:3000",
                    "api_token": "dag_good",
                }
            },
        )
    ]
    assert synced_env_values == [{"DAGSTER_ENDPOINT": "http://localhost:3000"}]
    assert synced_secrets == [("DAGSTER_API_TOKEN", "dag_good")]


def test_run_wizard_configures_github_mcp_and_sentry(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(
        [
            "quickstart",
            "anthropic",
            "api_key",
            "claude-opus-4-7",
            "github",
            "token",  # auth method (browser / token / none)
            "auto",
            "any",
            "summary",
        ]
    )
    text_responses = iter(
        [
            flow.DEFAULT_GITHUB_MCP_URL,
            "repos,issues,pull_requests,actions,search",
        ]
    )
    password_responses = iter(
        [
            "llm-secret",
            "ghp_test",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _github_configurator,
        "validate_github_mcp_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="GitHub MCP ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    # Persist goes through apply_setup → setup_flow writers (not the old
    # configurator-local sync_env_values / upsert_integration imports).
    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow,
        "sync_env_secret",
        lambda key, value: synced_env_secrets.append((key, value)),
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "github",
            {
                "credentials": {
                    "mode": flow.DEFAULT_GITHUB_MCP_MODE,
                    "url": flow.DEFAULT_GITHUB_MCP_URL,
                    "auth_token": "ghp_test",
                    "toolsets": "repos,issues,pull_requests,actions,search",
                    "username": None,
                }
            },
        ),
    ]
    assert synced_env_secrets == [("GITHUB_MCP_AUTH_TOKEN", "ghp_test")]
    assert synced_env_values == [
        {
            "GITHUB_MCP_MODE": flow.DEFAULT_GITHUB_MCP_MODE,
            "GITHUB_MCP_URL": flow.DEFAULT_GITHUB_MCP_URL,
            "GITHUB_MCP_TOOLSETS": "repos,issues,pull_requests,actions,search",
        },
    ]

    output = capsys.readouterr().out
    assert "GitHub MCP" in output


def test_run_wizard_reuses_saved_defaults_when_user_keeps_provider(monkeypatch, tmp_path) -> None:
    """When provider is already set and user declines to change, saved values are reused."""
    saved: dict[str, object] = {}
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, choices=None, default=None, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        selected_value = "skip" if "integration" in prompt.lower() else default
        if choices is not None:
            for choice in choices:
                if getattr(choice, "title", None) == selected_value:
                    selected_value = choice.value
                    break
        m.ask.return_value = selected_value
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        # "Change provider?" -> No (keep saved openai)
        m.ask.return_value = False
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: False)

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "quickstart"
    assert saved["provider"] == "openai"
    assert saved["model"] == "gpt-5.4-mini"
    assert "api_key" not in saved
    assert saved_llm_keys == [("openai", "saved-secret")]


def test_run_wizard_changes_model_when_user_keeps_provider(monkeypatch, tmp_path) -> None:
    """When provider is kept but user opts to change model, the new choice is saved."""
    saved: dict[str, object] = {}
    confirm_prompts: list[str] = []

    def _mock_select(*_args, choices=None, default=None, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "openai"
        elif "Choose OpenAI model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            # Falling through to `default` here picks "grafana local", whose configurator
            # runs `docker compose up -d` and then seeds a *live* Loki over the network —
            # neither of which this model-selection test has any interest in.
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = default
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        confirm_prompts.append(prompt)
        if "Change provider?" in prompt:
            m.ask.return_value = False
        elif "Change model?" in prompt:
            m.ask.return_value = True
        else:
            m.ask.return_value = False
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: True)

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["provider"] == "openai"
    assert saved["model"] == "gpt-5.4-mini"
    assert "Change model?" in confirm_prompts


def test_run_wizard_persists_matching_local_config_and_env(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "openai", "api_key", "gpt-5.4-mini", "skip"])
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "openai-secret"
        return m

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")

    assert payload["wizard"]["mode"] == "quickstart"
    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert "api_key" not in payload["targets"]["local"]

    assert "LLM_PROVIDER=openai\n" in env_values
    assert "OPENAI_API_KEY=" not in env_values
    assert saved_llm_keys == [("openai", "openai-secret")]


def test_run_wizard_codex_skips_api_key_and_runs_cli_onboarding(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "codex", "", "skip"])
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider, **_kwargs):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["codex"]
    assert saved_llm_keys == []

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "codex"
    assert payload["targets"]["local"]["api_key_env"] == ""
    assert payload["targets"]["local"]["model_env"] == "CODEX_MODEL"
    assert "LLM_PROVIDER=codex\n" in env_values
    assert "CODEX_MODEL=\n" in env_values
    assert "LLM_AUTH_METHOD=oauth\n" in env_values


def test_run_wizard_openai_oauth_is_onboarding_auth_method(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "openai", "oauth", "gpt-5.5", "skip"])
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []
    saved_summary: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider, **kwargs):
        cli_onboarding_providers.append(provider.value)
        assert kwargs["display_label"] == "OpenAI OAuth"
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )
    monkeypatch.setattr(
        flow, "_render_saved_summary", lambda **kwargs: saved_summary.update(kwargs)
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["codex"]
    assert saved_llm_keys == []

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["auth_method"] == "oauth"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "CODEX_MODEL"
    assert payload["targets"]["local"]["model"] == "gpt-5.5"
    assert "LLM_PROVIDER=openai\n" in env_values
    assert "LLM_AUTH_METHOD=oauth\n" in env_values
    assert "CODEX_MODEL=gpt-5.5\n" in env_values
    assert "OPENAI_API_KEY=" not in env_values
    assert saved_summary["provider_label"] == "OpenAI OAuth"
    assert saved_summary["credential_line"] == "OpenAI OAuth tokens (Codex CLI)"


def test_run_wizard_anthropic_oauth_is_onboarding_auth_method(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "oauth", "", "skip"])
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider, **kwargs):
        cli_onboarding_providers.append(provider.value)
        assert kwargs["display_label"] == "Anthropic OAuth"
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["claude-code"]

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "anthropic"
    assert payload["targets"]["local"]["auth_method"] == "oauth"
    assert payload["targets"]["local"]["api_key_env"] == "ANTHROPIC_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "CLAUDE_CODE_MODEL"
    assert "LLM_PROVIDER=anthropic\n" in env_values
    assert "LLM_AUTH_METHOD=oauth\n" in env_values
    assert "CLAUDE_CODE_MODEL=\n" in env_values


def test_run_wizard_claude_code_skips_api_key_and_runs_cli_onboarding(
    monkeypatch, tmp_path
) -> None:
    select_responses = iter(["quickstart", "claude-code", "", "skip"])
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider, **_kwargs):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["claude-code"]
    assert saved_llm_keys == []

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "claude-code"
    assert payload["targets"]["local"]["api_key_env"] == ""
    assert payload["targets"]["local"]["model_env"] == "CLAUDE_CODE_MODEL"
    assert "LLM_PROVIDER=claude-code\n" in env_values
    assert "CLAUDE_CODE_MODEL=\n" in env_values


def test_run_wizard_gemini_cli_skips_api_key_and_runs_cli_onboarding(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "gemini-cli", "", "skip"])
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider, **_kwargs):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["gemini-cli"]
    assert saved_llm_keys == []

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "gemini-cli"
    assert payload["targets"]["local"]["api_key_env"] == ""
    assert payload["targets"]["local"]["model_env"] == "GEMINI_CLI_MODEL"
    assert "LLM_PROVIDER=gemini-cli\n" in env_values
    assert "GEMINI_CLI_MODEL=\n" in env_values


def test_run_cli_llm_onboarding_ok_when_logged_in(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=True,
        detail="Logged in.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    result = flow._run_cli_llm_onboarding(provider)
    assert result == "ok"
    adapter.detect.assert_called_once()


def test_run_cli_llm_onboarding_repick_when_not_logged_in(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=False,
        detail="Not logged in.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_ok_after_login_retry(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(
                installed=True,
                logged_in=False,
                detail="Not logged in.",
            )
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "ok"
    assert len(detect_calls) == 2


def test_run_cli_llm_onboarding_launches_managed_codex_oauth(monkeypatch, tmp_path) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(
                installed=True,
                logged_in=False,
                bin_path="/usr/local/bin/codex",
                detail="Not logged in.",
            )
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.value = "codex"
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter
    oauth_calls: list[object] = []

    def _fake_codex_oauth_login() -> CodexOAuthResult:
        oauth_calls.append(None)
        return CodexOAuthResult(
            account_id="account-123",
            auth_path=tmp_path / "codex-home" / "auth.json",
            detail="OpenAI OAuth tokens stored for Codex.",
        )

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "login")
    monkeypatch.setattr(flow, "run_codex_oauth_login", _fake_codex_oauth_login)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))

    result = flow._run_cli_llm_onboarding(provider)

    assert result == "ok"
    assert oauth_calls == [None]
    assert len(detect_calls) == 1


def test_run_cli_llm_onboarding_surfaces_managed_codex_oauth_error(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=None,
        bin_path="/opt/homebrew/bin/codex",
        detail="Auth status unknown.",
    )
    provider = MagicMock()
    provider.value = "codex"
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    choices: list[str] = ["login", "repick"]

    def _choose(*_args, **_kwargs):
        return choices.pop(0)

    monkeypatch.setattr(flow, "_choose", _choose)
    monkeypatch.setattr(
        flow,
        "run_codex_oauth_login",
        MagicMock(side_effect=flow.CodexOAuthError("Could not bind localhost:1455.")),
    )
    monkeypatch.setattr(flow, "_run_interactive_login_process", MagicMock())

    result = flow._run_cli_llm_onboarding(provider, display_label="OpenAI OAuth")

    assert result == "repick"
    flow._run_interactive_login_process.assert_not_called()


def test_run_cli_llm_onboarding_launches_claude_browser_login(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "claude-code"
    adapter.binary_env_key = "CLAUDE_CODE_BIN"
    adapter.install_hint = "npm i -g @anthropic-ai/claude-code"
    adapter.auth_hint = "Run: claude auth login"
    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(
                installed=True,
                logged_in=False,
                bin_path="/opt/homebrew/bin/claude",
                detail="Not authenticated.",
            )
        return MagicMock(installed=True, logged_in=True, detail="Authenticated.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.value = "claude-code"
    provider.label = "Anthropic Claude Code CLI"
    provider.adapter_factory = lambda: adapter
    login_commands: list[list[str]] = []

    def _fake_login(command: list[str]) -> flow._LoginProcessResult:
        login_commands.append(command)
        return flow._LoginProcessResult(returncode=0)

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "login")
    monkeypatch.setattr(flow, "_run_interactive_login_process", _fake_login)

    result = flow._run_cli_llm_onboarding(provider)

    assert result == "ok"
    assert login_commands == [["/opt/homebrew/bin/claude", "auth", "login"]]
    assert len(detect_calls) == 1


def test_run_cli_llm_onboarding_repick_when_auth_status_unclear(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=None,
        detail="Auth status unknown.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_ok_after_unclear_auth_retry(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(
                installed=True,
                logged_in=None,
                detail="Auth status unknown.",
            )
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "ok"
    assert len(detect_calls) == 2


def test_run_cli_llm_onboarding_repick_when_user_chooses_repick(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=False,
        logged_in=None,
        detail="Not found.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_path_override_then_ok(monkeypatch, tmp_path) -> None:
    fake_bin = write_fake_runnable_cli_bin(tmp_path, "codex")

    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"

    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(installed=False, logged_in=None, detail="Not found.")
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "path")
    monkeypatch.setattr(flow, "_prompt_value", lambda *_args, **_kwargs: str(fake_bin))
    monkeypatch.setattr(flow, "sync_env_values", lambda *_args, **_kwargs: None)

    original_codex_bin = os.environ.get("CODEX_BIN")
    try:
        result = flow._run_cli_llm_onboarding(provider)

        assert result == "ok"
        assert len(detect_calls) == 2
        # os.environ must be updated in-process so the next detect() call in the
        # retry loop resolves the new binary without a process restart.
        assert os.environ.get("CODEX_BIN") == str(fake_bin)
    finally:
        if original_codex_bin is None:
            os.environ.pop("CODEX_BIN", None)
        else:
            os.environ["CODEX_BIN"] = original_codex_bin


def test_run_cli_llm_onboarding_abort_after_max_retries(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=False,
        logged_in=None,
        detail="Not found.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "abort"
    assert adapter.detect.call_count == 10


def test_credential_line_for_saved_summary_cli_codex() -> None:
    from surfaces.cli.wizard import config as wizard_config

    codex = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "codex")
    assert (
        llm_credential._credential_line_for_saved_summary(codex)
        == "OpenAI Codex CLI (Run: codex login)"
    )


def test_credential_line_for_saved_summary_cli_claude_code() -> None:
    from surfaces.cli.wizard import config as wizard_config

    claude_code = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "claude-code")
    assert llm_credential._credential_line_for_saved_summary(claude_code) == (
        "Anthropic Claude Code CLI (Run: claude auth login or set ANTHROPIC_API_KEY)"
    )


def test_credential_line_for_saved_summary_cli_gemini_cli() -> None:
    from surfaces.cli.wizard import config as wizard_config

    gemini_cli = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "gemini-cli")
    assert llm_credential._credential_line_for_saved_summary(gemini_cli) == (
        "Google Gemini CLI (Run: gemini (interactive login) or set GEMINI_API_KEY)"
    )


def test_credential_line_for_saved_summary_cli_copilot() -> None:
    from surfaces.cli.wizard import config as wizard_config

    copilot = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "copilot")
    line = llm_credential._credential_line_for_saved_summary(copilot)
    # PR #1533: hint surfaces both CLI paths (`copilot login`, `gh auth login`)
    # before the env-var bypass, matching the new CLI-first probe order.
    assert line.startswith("GitHub Copilot CLI (Run `copilot login`")
    assert "gh auth login" in line
    assert "COPILOT_GITHUB_TOKEN" in line


def test_credential_line_for_saved_summary_anthropic() -> None:
    from surfaces.cli.wizard import config as wizard_config

    anthropic = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "anthropic")
    assert llm_credential._credential_line_for_saved_summary(anthropic) == "system keychain"


def test_credential_line_for_saved_summary_cli_without_factory() -> None:
    from surfaces.cli.wizard.config import ModelOption, ProviderOption

    p = ProviderOption(
        value="codex",
        label="Fake CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CODEX_MODEL",
        default_model="",
        models=(ModelOption(value="", label="default"),),
        credential_kind="cli",
        adapter_factory=None,
        allow_custom_models=True,
    )
    assert llm_credential._credential_line_for_saved_summary(p) == "Fake CLI (CLI)"


def test_run_wizard_configures_gitlab(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "gitlab"])
    password_responses = iter(["llm-secret", "glpat_test"])
    text_responses = iter(["https://gitlab.example.com/api/v4"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _gitlab_configurator,
        "GITLAB_SETUP",
        dataclasses.replace(
            _gitlab_configurator.GITLAB_SETUP,
            verify=lambda _source, _config: {"status": "passed", "detail": "GitLab ok"},
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    def _sync_env_secret(key: str, value: str) -> None:
        synced_env_secrets.append((key, value))

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(_setup_flow, "sync_env_secret", _sync_env_secret)
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "gitlab",
            {
                "credentials": {
                    "base_url": "https://gitlab.example.com/api/v4",
                    "auth_token": "glpat_test",
                }
            },
        )
    ]
    assert synced_env_secrets == [("GITLAB_ACCESS_TOKEN", "glpat_test")]
    assert synced_env_values == [
        {
            "GITLAB_BASE_URL": "https://gitlab.example.com/api/v4",
        }
    ]


def test_run_wizard_gitlab_retries_on_validation_failure(monkeypatch, tmp_path) -> None:
    """When GitLab validation fails the first time, the wizard retries and succeeds."""
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "gitlab"])
    # First attempt: wrong token; second attempt: correct token
    password_responses = iter(["llm-secret", "bad_token", "glpat_good"])
    # base_url is prompted on each retry
    text_responses = iter(
        [
            "https://gitlab.com/api/v4",
            "https://gitlab.com/api/v4",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []
    validation_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _verify_gitlab(_source, _config):
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count == 1:
            return {"status": "failed", "detail": "Unauthorized"}
        return {"status": "passed", "detail": "GitLab ok"}

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _gitlab_configurator,
        "GITLAB_SETUP",
        dataclasses.replace(_gitlab_configurator.GITLAB_SETUP, verify=_verify_gitlab),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    def _sync_env_secret(key: str, value: str) -> None:
        synced_env_secrets.append((key, value))

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(_setup_flow, "sync_env_secret", _sync_env_secret)
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert validation_call_count == 2
    assert saved_integrations == [
        (
            "gitlab",
            {
                "credentials": {
                    "base_url": "https://gitlab.com/api/v4",
                    "auth_token": "glpat_good",
                }
            },
        )
    ]
    assert synced_env_secrets == [("GITLAB_ACCESS_TOKEN", "glpat_good")]
    assert synced_env_values == [
        {
            "GITLAB_BASE_URL": "https://gitlab.com/api/v4",
        }
    ]


def test_run_wizard_switches_provider_and_keeps_store_and_env_in_sync(
    monkeypatch, tmp_path
) -> None:
    # Saved: anthropic. User says yes to "Change provider?" and picks openai.
    select_responses = iter(["quickstart", "openai", "api_key", "gpt-5.4-mini", "skip"])
    confirm_responses = iter([True])  # "Change provider?" -> Yes
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(confirm_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "fresh-openai-key"
        return m

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"
    wizard_store.save_local_config(
        wizard_mode="quickstart",
        provider="anthropic",
        model="claude-opus-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        probes={
            "local": {"target": "local", "reachable": True, "detail": "ok"},
            "remote": {"target": "remote", "reachable": False, "detail": "down"},
        },
        path=store_path,
    )
    env_path.write_text(
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=saved-anthropic-key\n"
        "ANTHROPIC_MODEL=claude-opus-4-5\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(_ui, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")

    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert "api_key" not in payload["targets"]["local"]

    assert "LLM_PROVIDER=openai\n" in env_values
    assert "OPENAI_API_KEY=" not in env_values
    assert "ANTHROPIC_API_KEY=" not in env_values
    assert "OPENAI_REASONING_MODEL=" in env_values
    assert saved_llm_keys == [("openai", "fresh-openai-key")]


def test_run_wizard_configures_opensearch(monkeypatch, tmp_path) -> None:
    """Happy path: URL + basic-auth mode persists through apply_setup."""
    select_responses = iter(
        ["quickstart", "anthropic", "api_key", "claude-opus-4-7", "opensearch", "basic"]
    )
    # basic mode prompts url + username (text) and password (secret); api_key is
    # not asked (it belongs to another mode) and clears.
    password_responses = iter(["llm-secret", "secret-pass"])
    text_responses = iter(["https://my-cluster.example.com", "admin"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "OPENSEARCH_SETUP",
        dataclasses.replace(
            _observability_configurator.OPENSEARCH_SETUP,
            verify=lambda _source, _config: {"status": "passed", "detail": "OpenSearch ok"},
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow, "sync_env_secret", lambda key, value: synced_env_secrets.append((key, value))
    )
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "opensearch",
            {
                "credentials": {
                    "url": "https://my-cluster.example.com",
                    "api_key": None,
                    "username": "admin",
                    "password": "secret-pass",
                }
            },
        )
    ]
    assert synced_env_secrets == [
        ("OPENSEARCH_API_KEY", ""),
        ("OPENSEARCH_PASSWORD", "secret-pass"),
    ]
    assert synced_env_values == [
        {
            "OPENSEARCH_URL": "https://my-cluster.example.com",
            "OPENSEARCH_USERNAME": "admin",
        }
    ]


def test_run_wizard_opensearch_retries_on_validation_failure(monkeypatch, tmp_path) -> None:
    """When OpenSearch verification fails the first time, the wizard retries and succeeds."""
    # basic mode is re-picked on the retry.
    select_responses = iter(
        ["quickstart", "anthropic", "api_key", "claude-opus-4-7", "opensearch", "basic", "basic"]
    )
    password_responses = iter(["llm-secret", "wrong-pass", "correct-pass"])
    text_responses = iter(
        [
            "https://my-cluster.example.com",
            "admin",
            "https://my-cluster.example.com",
            "admin",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    verification_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _verify_opensearch(_source, _config):
        nonlocal verification_call_count
        verification_call_count += 1
        if verification_call_count == 1:
            return {"status": "failed", "detail": "HTTP 401: unauthorized"}
        return {"status": "passed", "detail": "OpenSearch ok"}

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "OPENSEARCH_SETUP",
        dataclasses.replace(
            _observability_configurator.OPENSEARCH_SETUP, verify=_verify_opensearch
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)
    monkeypatch.setattr(_setup_flow, "sync_env_secret", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert verification_call_count == 2
    assert saved_integrations == [
        (
            "opensearch",
            {
                "credentials": {
                    "url": "https://my-cluster.example.com",
                    "api_key": None,
                    "username": "admin",
                    "password": "correct-pass",
                }
            },
        )
    ]


def test_run_wizard_opensearch_allows_url_only_when_auth_blank(monkeypatch, tmp_path) -> None:
    """Blank API key and blank basic auth is intentional (no-auth / gateway).

    The "none" mode configures the cluster URL-only; the auth fields are cleared,
    not prompted.
    """
    select_responses = iter(
        ["quickstart", "anthropic", "api_key", "claude-opus-4-7", "opensearch", "none"]
    )
    password_responses = iter(["llm-secret"])
    text_responses = iter(["https://my-cluster.example.com"])
    saved_integrations: list[tuple[str, dict]] = []
    verification_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _verify_opensearch(_source, config):
        # Mirror verify_opensearch's basic-auth completeness check (no network).
        nonlocal verification_call_count
        verification_call_count += 1
        username = config.get("username", "")
        password = config.get("password", "")
        if bool(username) != bool(password):
            return {"status": "failed", "detail": "Provide both username and password."}
        return {"status": "passed", "detail": "OpenSearch ok"}

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "OPENSEARCH_SETUP",
        dataclasses.replace(
            _observability_configurator.OPENSEARCH_SETUP, verify=_verify_opensearch
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)
    monkeypatch.setattr(_setup_flow, "sync_env_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_setup_flow, "sync_env_values", lambda *_a, **_kw: tmp_path / ".env")
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert verification_call_count == 1
    assert saved_integrations == [
        (
            "opensearch",
            {
                "credentials": {
                    "url": "https://my-cluster.example.com",
                    "api_key": None,
                    "username": None,
                    "password": None,
                }
            },
        )
    ]


def test_run_wizard_opensearch_rejects_empty_basic_password(monkeypatch, tmp_path) -> None:
    """Username without password is rejected by the verifier.

    Companion regression for the half-credential bug: a username alone would
    persist and omit Authorization at runtime. verify_opensearch requires both
    basic-auth halves (or neither), so the wizard re-prompts on the first
    attempt and only persists once both are supplied.
    """
    # basic mode both times: username without a password fails verification; the
    # retry supplies the password.
    select_responses = iter(
        ["quickstart", "anthropic", "api_key", "claude-opus-4-7", "opensearch", "basic", "basic"]
    )
    # First attempt: username, empty password → verifier fails.
    # Retry: username, real password → verifier passes.
    password_responses = iter(["llm-secret", "", "real-pass"])
    text_responses = iter(
        [
            "https://my-cluster.example.com",
            "admin",
            "https://my-cluster.example.com",
            "admin",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    verification_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _verify_opensearch(_source, config):
        # Mirror verify_opensearch's basic-auth completeness check (no network).
        nonlocal verification_call_count
        verification_call_count += 1
        username = config.get("username", "")
        password = config.get("password", "")
        if bool(username) != bool(password):
            return {"status": "failed", "detail": "Provide both username and password."}
        return {"status": "passed", "detail": "OpenSearch ok"}

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        _observability_configurator,
        "OPENSEARCH_SETUP",
        dataclasses.replace(
            _observability_configurator.OPENSEARCH_SETUP, verify=_verify_opensearch
        ),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)
    monkeypatch.setattr(_setup_flow, "sync_env_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_setup_flow, "sync_env_values", lambda *_a, **_kw: tmp_path / ".env")
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    # First attempt fails the verifier's basic-auth check; the retry passes.
    assert verification_call_count == 2
    assert saved_integrations == [
        (
            "opensearch",
            {
                "credentials": {
                    "url": "https://my-cluster.example.com",
                    "api_key": None,
                    "username": "admin",
                    "password": "real-pass",
                }
            },
        )
    ]


def test_run_wizard_configures_telegram(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "telegram"])
    password_responses = iter(["llm-secret", "123:ABC"])
    text_responses = iter(["-1001234567890"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    _stub_telegram_setup(
        monkeypatch,
        lambda _source, _config: {
            "status": "passed",
            "detail": "Connected to Telegram bot @opensre_bot.",
        },
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    def _sync_env_secret(key: str, value: str) -> None:
        synced_env_secrets.append((key, value))

    monkeypatch.setattr(_setup_flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(_setup_flow, "sync_env_secret", _sync_env_secret)
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "telegram",
            {
                "credentials": {
                    "bot_token": "123:ABC",
                    "default_chat_id": "-1001234567890",
                }
            },
        )
    ]
    assert synced_env_secrets == [("TELEGRAM_BOT_TOKEN", "123:ABC")]
    assert synced_env_values == [{"TELEGRAM_DEFAULT_CHAT_ID": "-1001234567890"}]


def test_run_wizard_telegram_retries_on_validation_failure(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "telegram"])
    password_responses = iter(["llm-secret", "bad-token", "123:GOOD"])
    text_responses = iter(["-1001", "-1001"])
    saved_integrations: list[tuple[str, dict]] = []
    validation_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _validate(_source, _config):
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count == 1:
            return {"status": "failed", "detail": "Telegram API check failed."}
        return {"status": "passed", "detail": "Connected to Telegram bot @bot."}

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    _stub_telegram_setup(monkeypatch, _validate)
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_keyring_secret", _stub_save)
    monkeypatch.setattr(_setup_flow, "sync_env_values", lambda *_a, **_kw: tmp_path / ".env")
    monkeypatch.setattr(_setup_flow, "sync_env_secret", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _setup_flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert validation_call_count == 2
    assert saved_integrations[0][0] == "telegram"
    assert saved_integrations[0][1]["credentials"]["bot_token"] == "123:GOOD"


def test_persist_llm_credential_host_kind_writes_env_not_keyring(monkeypatch, tmp_path) -> None:
    """#3291: a host credential lands where the runtime reads it (.env), never the keyring."""
    from surfaces.cli.wizard.config import PROVIDER_BY_VALUE

    synced: dict[str, str] = {}
    monkeypatch.setattr(
        llm_credential, "sync_env_values", lambda values: synced.update(values) or tmp_path / ".env"
    )
    keyring_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        llm_credential,
        "_persist_llm_api_key",
        lambda env, val: keyring_calls.append((env, val)) or True,
    )
    monkeypatch.setenv("OLLAMA_HOST", "sentinel-before")

    assert llm_credential._persist_llm_credential(
        PROVIDER_BY_VALUE["ollama"], "http://10.0.0.5:11434"
    )

    assert synced == {"OLLAMA_HOST": "http://10.0.0.5:11434"}
    assert keyring_calls == []
    assert os.environ["OLLAMA_HOST"] == "http://10.0.0.5:11434"


def test_persist_llm_credential_secret_kind_keeps_keyring(monkeypatch, tmp_path) -> None:
    from surfaces.cli.wizard.config import PROVIDER_BY_VALUE

    monkeypatch.setattr(
        llm_credential, "sync_env_values", lambda _values: pytest.fail("secret must not hit .env")
    )
    keyring_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        llm_credential,
        "_persist_llm_api_key",
        lambda env, val: keyring_calls.append((env, val)) or True,
    )

    assert llm_credential._persist_llm_credential(PROVIDER_BY_VALUE["anthropic"], "sk-test")

    assert keyring_calls == [("ANTHROPIC_API_KEY", "sk-test")]


def test_local_defaults_host_kind_ignores_stale_keyring_entry(monkeypatch, tmp_path) -> None:
    """#3291 trap half: a keyring-only host must NOT read as configured — the runtime
    cannot see it, so the wizard must re-prompt instead of skipping."""
    store = tmp_path / "opensre.json"
    store.write_text(json.dumps({"targets": {"local": {"provider": "ollama"}}}))
    monkeypatch.setattr(_ui, "get_store_path", lambda: store)
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: True)  # stale keyring entry

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert _ui._local_defaults()["has_api_key"] is False

    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")
    assert _ui._local_defaults()["has_api_key"] is True


def test_run_wizard_host_kind_does_not_migrate_legacy_api_key(monkeypatch, tmp_path) -> None:
    """#3291 (Greptile P1): a saved Ollama config carrying a stale legacy ``api_key`` must NOT
    have that value migrated into ``OLLAMA_HOST`` — a host is not a secret api key, and writing a
    secret-shaped legacy value to ``.env`` would both leak it in plaintext and point the runtime
    at a bogus host. The wizard must fall through to the host-URL prompt instead."""
    store = tmp_path / "opensre.json"
    store.write_text(
        json.dumps(
            {
                "targets": {
                    "local": {
                        "provider": "ollama",
                        "model": "llama3.1",
                        "auth_method": "api_key",
                        "api_key": "sk-stale-legacy-secret",
                    }
                }
            }
        )
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: store)
    monkeypatch.setattr(flow, "get_store_path", lambda: store)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _p: ProbeResult("local", True, "ok"))

    def _mock_select(*_args, **_kwargs):  # quickstart setup mode
        m = MagicMock()
        m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):  # "Change provider?" -> No: keep the saved ollama
        m = MagicMock()
        m.ask.return_value = False
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)

    prompted: list[str] = []

    def _fake_prompt(label, *_args, **_kwargs):
        prompted.append(label)
        return "http://10.0.0.9:11434"  # the host the user actually enters

    monkeypatch.setattr(llm_credential, "_prompt_value", _fake_prompt)

    persisted: list[tuple[str, str]] = []

    def _fake_persist(provider, value):
        persisted.append((provider.value, value))
        return False  # stop the wizard right after the credential decision

    monkeypatch.setattr(llm_credential, "_persist_llm_credential", _fake_persist)

    exit_code = flow.run_wizard()

    # The stale legacy api_key was never routed into OLLAMA_HOST; the wizard prompted for the
    # host and would persist the entered value, not the legacy secret.
    assert prompted, "a host-kind provider must fall through to the host-URL prompt"
    assert ("ollama", "sk-stale-legacy-secret") not in persisted
    assert persisted == [("ollama", "http://10.0.0.9:11434")]
    assert exit_code == 1  # _fake_persist returned False to short-circuit the run


def test_run_wizard_llm_key_retries_on_validation_failure(monkeypatch, tmp_path, capsys) -> None:
    """Two consecutive LLM key validation failures, then success.

    Mirrors test_run_wizard_dagster_retries_on_validation_failure: the wizard
    recovers from N consecutive failures and only the final validated key
    reaches the persistence layer.
    """
    menu_responses = iter(["retry", "retry"])
    password_responses = iter(["sk-bad-1", "sk-bad-2", "sk-good"])
    validator_calls: list[tuple[str, str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    call_order: list[str] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key, model))
        call_order.append(f"validate:{api_key}")
        if len(validator_calls) < 3:
            return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        saved_llm_keys.append((provider, value))
        call_order.append(f"persist:{value}")
        return _KEYRING_SAVE

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)

    exit_code = flow.run_wizard()

    assert validator_calls == [
        ("anthropic", "sk-bad-1", "claude-opus-4-7"),
        ("anthropic", "sk-bad-2", "claude-opus-4-7"),
        ("anthropic", "sk-good", "claude-opus-4-7"),
    ]
    assert saved_llm_keys == [("anthropic", "sk-good")]
    assert call_order[-2:] == ["validate:sk-good", "persist:sk-good"]
    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.count("Anthropic rejected the API key.") >= 2
    assert output.count("Failed") >= 2
    assert "Summary" in output
    assert "Done." in output


def test_run_wizard_saved_provider_missing_key_validates_on_reentry(monkeypatch, tmp_path) -> None:
    """The saved-provider re-entry key prompt validates with the saved model."""
    menu_responses = iter(["retry"])
    password_responses = iter(["sk-bad", "sk-good"])
    validator_calls: list[tuple[str, str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key, model))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="OpenAI rejected the API key.")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    # Deliberately NOT the openai default model: proves the saved
                    # model (not default_model) reaches the validator on this path.
                    "model": "gpt-5.3-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validator_calls == [
        ("openai", "sk-bad", "gpt-5.3-mini"),
        ("openai", "sk-good", "gpt-5.3-mini"),
    ]
    assert saved_llm_keys == [("openai", "sk-good")]
    assert exit_code == 0


def test_run_wizard_llm_key_save_anyway_persists_unvalidated_key(
    monkeypatch, tmp_path, capsys
) -> None:
    """Offline escape hatch: save-anyway persists the key without re-validating."""
    menu_responses = iter(["save_anyway"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-offline"
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=False, detail="Validation request failed: network unreachable")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validation_call_count == 1
    assert saved_llm_keys == [("anthropic", "sk-offline")]
    output = capsys.readouterr().out
    assert "Saved ANTHROPIC_API_KEY without validating it." in output
    assert exit_code == 0


def test_run_wizard_llm_key_repick_returns_to_provider_menu(monkeypatch, tmp_path) -> None:
    """Repick from a failed key re-renders the provider menu; nothing persisted."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["repick"])
    password_responses = iter(["sk-fail-anthropic", "sk-openai-good"])
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kwargs: synced_env_values.append(values) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )
    exit_code = flow.run_wizard()

    assert select_prompts.count("Choose your LLM provider") == 2
    assert saved_llm_keys == [("openai", "sk-openai-good")]
    assert all("sk-fail-anthropic" not in str(values) for values in synced_env_values)
    assert exit_code == 0


def test_run_wizard_llm_key_valid_first_try_validates_once(monkeypatch, tmp_path, capsys) -> None:
    """Happy path: exactly one validation, before persist, and no new prompts."""
    select_responses = iter(["quickstart", "anthropic", "api_key", "claude-opus-4-7", "skip"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []
    call_order: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        call_order.append(f"validate:{api_key}")
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        saved_llm_keys.append((provider, value))
        call_order.append(f"persist:{value}")
        return _KEYRING_SAVE

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)

    exit_code = flow.run_wizard()

    assert validation_call_count == 1
    assert saved_llm_keys == [("anthropic", "sk-good")]
    assert call_order == ["validate:sk-good", "persist:sk-good"]
    assert next(select_responses, None) is None  # no leftover prompt: no new menus
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Summary" in output
    assert "Done." in output
    assert output.index("Summary") < output.index("Done.")


def test_run_wizard_azure_llm_key_validation_runs_after_endpoint_prompt(
    monkeypatch, tmp_path
) -> None:
    """Azure: AZURE_OPENAI_BASE_URL must be populated BEFORE the validator runs."""
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION")

    endpoint_env_at_validation: list[str | None] = []
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    synced_provider_env: list[dict[str, object]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "azure-openai"
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "az-key"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "https://myres.openai.azure.com"
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        endpoint_env_at_validation.append(os.environ.get("AZURE_OPENAI_BASE_URL"))
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    def _sync_provider_env(**kwargs):
        synced_provider_env.append(kwargs)
        return tmp_path / ".env"

    # Azure deployment discovery calls the live resource; stub it so the picker offers
    # the deployment as a menu choice instead of hitting the network (#4117).
    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", _sync_provider_env)
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert endpoint_env_at_validation == ["https://myres.openai.azure.com"]
    assert validator_calls == [("azure-openai", "az-key")]
    assert saved_llm_keys == [("azure-openai", "az-key")]
    extra_env = synced_provider_env[0]["extra_env"]
    assert isinstance(extra_env, dict)
    assert extra_env["AZURE_OPENAI_BASE_URL"] == "https://myres.openai.azure.com"
    assert exit_code == 0


def test_run_wizard_cli_provider_skips_llm_key_validation(monkeypatch, tmp_path) -> None:
    """Scope gate: a CLI-kind provider never triggers key validation.

    The api_key provider tried (and failed) first in the same run proves the
    validator is live and the repick landed on the CLI path — killing the
    tautology where "never validates" holds only because nothing validates.
    """
    provider_responses = iter(["anthropic", "claude-code"])
    menu_responses = iter(["repick"])
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = ""
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-bad-anthropic"
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")

    def _cli_onboarding(provider, **_kwargs):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validator_calls == [("anthropic", "sk-bad-anthropic")]
    assert cli_onboarding_providers == ["claude-code"]
    assert saved_llm_keys == []
    assert exit_code == 0


def test_run_wizard_ollama_host_is_validated_then_persisted_to_dotenv(
    monkeypatch, tmp_path
) -> None:
    """A host-kind credential (Ollama) IS probed — and still lands in .env, not the keyring.

    Ollama is the one provider whose credential is a host URL rather than a secret, and
    it is exactly the provider a live probe helps most: an unreachable host or an
    un-pulled model is otherwise only discovered later, at the first investigation.
    ``validate_provider_credentials`` already routes ``ollama`` to ``_check_ollama``.

    The api_key provider that validated (and failed) first in the run proves the
    validator is live; the repicked Ollama host is then probed against the model the
    user actually picked and persisted to .env, with nothing written to the keyring.
    """
    provider_responses = iter(["anthropic", "ollama"])
    menu_responses = iter(["repick"])
    validator_calls: list[tuple[str, str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    synced_env_values: list[dict[str, str]] = []

    monkeypatch.setenv("OLLAMA_HOST", "sentinel-before")

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "llama3.2" if "Ollama" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-bad-anthropic"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "http://127.0.0.1:11434"
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key, model))
        if provider.value == "ollama":
            return ValidationResult(ok=True, detail=f"Ollama reachable. Model '{model}' is ready.")
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kwargs: synced_env_values.append(values) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validator_calls == [
        ("anthropic", "sk-bad-anthropic", "claude-opus-4-7"),
        ("ollama", "http://127.0.0.1:11434", "llama3.2"),
    ]
    assert synced_env_values == [{"OLLAMA_HOST": "http://127.0.0.1:11434"}]
    assert os.environ["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert saved_llm_keys == []  # a host URL is config, never a keyring secret
    assert exit_code == 0


def test_run_wizard_ollama_host_env_write_failure_offers_recovery_menu(
    monkeypatch, tmp_path
) -> None:
    """#3591 (cerencamkiran): a host credential whose .env write fails reaches the shared
    recovery menu, it does not crash the wizard.

    ``_persist_llm_credential``'s host branch writes the Ollama host to ``.env`` via
    ``sync_env_values``. If that write raises (permission denied, read-only fs, a full
    disk — any ``OSError``), the user must see the same Retry / Continue-without-saving
    / Pick-a-different-provider menu the keyring path already offers, and choosing
    "Continue without saving (this session only)" must finish onboarding with the host
    exported to ``os.environ`` only — never with the write error propagating.

    Today the host branch does not catch the write error, so the ``OSError`` escapes
    ``_persist_llm_credential_with_recovery`` and crashes onboarding. This test is RED
    until that gap is closed.
    """
    menu_responses = iter(["continue_unsaved"])
    recovery_prompts: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    monkeypatch.setenv("OLLAMA_HOST", "sentinel-before")

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "could not be saved" in prompt:
            recovery_prompts.append(prompt)
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "ollama"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "llama3.2"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "http://127.0.0.1:11434"
        return m

    def _raise_env_write(_values, **_kwargs):
        # Simulate a real .env write failure: no write permission / read-only fs / full disk.
        raise OSError("[Errno 13] Permission denied: '.env'")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(llm_credential, "sync_env_values", _raise_env_write)
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    # RED expectation: the write error is caught and turned into the shared recovery menu,
    # not propagated. Today ``run_wizard`` raises the OSError here instead.
    exit_code = flow.run_wizard()

    assert recovery_prompts, "a failed .env write must surface the shared recovery menu"
    # "Continue without saving (this session only)" exports the host process-locally only.
    assert os.environ["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert saved_llm_keys == []  # a host URL is config, never a keyring secret
    assert exit_code == 0


def test_run_wizard_saved_provider_with_stored_key_skips_validation(monkeypatch, tmp_path) -> None:
    """REGRESSION GATE: a stored key means no key prompt and no validator call."""
    password_prompts: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        m.ask.return_value = "skip" if "integration" in prompt.lower() else "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "must-not-be-prompted"
        return m

    def _validate(**_kwargs):
        raise AssertionError("validator must not be called for a stored key")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate, raising=False)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: True)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert password_prompts == []
    assert saved_llm_keys == []


def test_run_wizard_legacy_key_migration_does_not_validate(monkeypatch, tmp_path) -> None:
    """REGRESSION GATE: silent legacy-store migration persists without validating."""
    password_prompts: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        m.ask.return_value = "skip" if "integration" in prompt.lower() else "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "must-not-be-prompted"
        return m

    def _validate(**_kwargs):
        raise AssertionError("validator must not be called for a legacy migration")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate, raising=False)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert password_prompts == []
    assert saved_llm_keys == [("openai", "saved-secret")]


def test_run_wizard_llm_key_persist_failure_offers_recovery_menu(
    monkeypatch, tmp_path, capsys
) -> None:
    """Keyring persist failure offers retry instead of hard-exiting with 1."""
    select_prompts: list[str] = []
    menu_responses = iter(["retry"])
    validation_call_count = 0
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert persist_attempts == [("anthropic", "sk-good"), ("anthropic", "sk-good")]
    assert exit_code == 0
    assert validation_call_count == 1  # validation is NOT re-run on persist retry
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    output = capsys.readouterr().out
    assert "OpenSRE could not save your API key to secure local storage." in output


def test_run_wizard_llm_key_persist_failure_repick_returns_to_provider_menu(
    monkeypatch, tmp_path
) -> None:
    """Repick from the persist-failure menu re-renders the provider menu."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["repick"])
    validation_call_count = 0
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    password_responses = iter(["sk-anthropic", "sk-openai"])

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=True, detail="API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert select_prompts.count("Choose your LLM provider") == 2
    assert persist_attempts == [("anthropic", "sk-anthropic"), ("openai", "sk-openai")]
    assert validation_call_count == 2
    assert exit_code == 0


def test_run_wizard_llm_key_persist_failure_abort_exits_nonzero(
    monkeypatch, tmp_path, capsys
) -> None:
    """Ctrl+C at the persist-failure menu cancels setup with exit code 1."""
    select_prompts: list[str] = []
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = None  # Ctrl+C at the recovery menu
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    output = capsys.readouterr().out
    assert "Setup cancelled." in output
    assert exit_code == 1
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    assert persist_attempts == [("anthropic", "sk-good")]


def test_run_wizard_llm_key_back_at_reprompt_returns_to_provider_menu(
    monkeypatch, tmp_path, capsys
) -> None:
    """WizardBack at the key re-prompt returns to the provider menu, not cancel."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["retry"])
    password_responses = iter(["sk-bad", None, "sk-openai-good"])
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert select_prompts.count("Choose your LLM provider") == 2
    assert [call[1] for call in validator_calls] == ["sk-bad", "sk-openai-good"]
    assert saved_llm_keys == [("openai", "sk-openai-good")]
    output = capsys.readouterr().out
    assert "Setup cancelled." not in output
    assert exit_code == 0


def test_run_wizard_llm_key_ctrl_c_at_retry_menu_cancels_setup(
    monkeypatch, tmp_path, capsys
) -> None:
    """Ctrl+C at the validation-failure menu cancels setup; nothing persisted."""
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = None  # Ctrl+C at the retry menu
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-bad"
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=False, detail="Anthropic rejected the API key."),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Setup cancelled." in output
    assert saved_llm_keys == []


def test_run_wizard_llm_key_validator_exception_treated_as_failure(
    monkeypatch, tmp_path, capsys
) -> None:
    """A raising validator renders as a failure instead of crashing the wizard."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["repick"])
    password_responses = iter(["sk-anthropic", "sk-openai"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count == 1:
            raise RuntimeError("boom")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validation_call_count == 2
    output = capsys.readouterr().out
    assert "boom" in output
    # The raising probe is call #1 — Anthropic — so Anthropic is the provider named in
    # the failure menu. OpenAI is the provider the user repicks, and it validates cleanly.
    assert "Anthropic API key could not be verified. What next?" in select_prompts
    assert saved_llm_keys == [("openai", "sk-openai")]
    assert exit_code == 0


def test_run_wizard_llm_key_validation_failure_output_masks_secret(
    monkeypatch, tmp_path, capsys
) -> None:
    """The fail -> retry -> save-anyway path never leaks the key to any surface."""
    secret = "sk-super-secret-XYZ"
    menu_responses = iter(["retry", "save_anyway"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []
    password_calls: list[dict[str, object]] = []
    select_renderings: list[str] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        rendered = [prompt]
        for choice in _kwargs.get("choices") or []:
            rendered.append(str(getattr(choice, "title", "")))
            rendered.append(str(getattr(choice, "description", "")))
        select_renderings.append(" ".join(rendered))
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        password_calls.append({"label": str(_args[0]) if _args else "", **_kwargs})
        m = MagicMock()
        m.ask.return_value = secret
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validation_call_count == 2
    captured = capsys.readouterr()
    assert "Saved ANTHROPIC_API_KEY without validating it." in captured.out
    assert secret not in captured.out
    assert secret not in captured.err
    assert all(secret not in str(call.get("default", "")) for call in password_calls)
    assert all(secret not in str(call.get("instruction") or "") for call in password_calls)
    assert all(secret not in rendered for rendered in select_renderings)
    assert saved_llm_keys == [("anthropic", secret)]
    assert exit_code == 0


# ---------------------------------------------------------------------------
# DELTA 3 — validation must probe the model the user actually picked.
#
# Today `run_wizard` prompts for + validates the credential BEFORE `_choose_model`
# runs (flow.py: credential block ~920-974, model selection ~976-1003), so the
# validator is handed `model_provider.default_model` (change-provider branch) or
# the *stale* saved model (saved-provider branch) instead of the model that is
# subsequently persisted. GREEN hoists the model selection above the credential
# block in BOTH branches.
# ---------------------------------------------------------------------------


def test_run_wizard_validates_the_model_the_user_picked_change_provider(
    monkeypatch, tmp_path
) -> None:
    """Change-provider branch: the validated model is the PICKED model, not the default.

    Anthropic's ``default_model`` is ``claude-opus-4-7``; the user picks
    ``claude-haiku-4-5``. The credential must be probed against the model that is
    actually written to the store, otherwise a key that is not entitled to the
    default model is reported as invalid.
    """
    observed_models: list[str] = []
    saved: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-haiku-4-5"  # NOT the default (claude-opus-4-7)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        observed_models.append(model)
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _stub_save)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["model"] == "claude-haiku-4-5"
    # The model that was VALIDATED must be byte-for-byte the model that was PERSISTED.
    assert observed_models == ["claude-haiku-4-5"]
    assert "claude-opus-4-7" not in observed_models


def test_run_wizard_validates_the_model_the_user_picked_saved_provider(
    monkeypatch, tmp_path
) -> None:
    """Saved-provider branch: changing the model must change the model that is validated.

    The user keeps the saved provider (openai), re-enters the key because none is
    stored, and then changes the model from the saved ``gpt-5.4-mini`` to
    ``gpt-5.6-sol``. The probe must use ``gpt-5.6-sol`` — the value that is
    persisted — not the stale saved model.
    """
    observed_models: list[str] = []
    saved: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "model" in prompt:
            m.ask.return_value = "gpt-5.6-sol"  # NOT the saved model (gpt-5.4-mini)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        m.ask.return_value = "Change model?" in prompt  # keep provider, change model
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        observed_models.append(model)
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _stub_save)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["model"] == "gpt-5.6-sol"
    assert observed_models == ["gpt-5.6-sol"]
    assert "gpt-5.4-mini" not in observed_models


def test_run_wizard_ollama_validates_the_selected_model_not_the_default(
    monkeypatch, tmp_path
) -> None:
    """LOCKOUT REGRESSION GATE (AC-2): Ollama is probed against the SELECTED model.

    ``_check_ollama`` (validation.py) hard-fails when the probed model is not
    pulled: ``Model '<model>' not found. Run: ollama pull <model>``. If the wizard
    probes ``default_model`` (``llama3.2``) instead of the model the user picked
    (``qwen2.5:7b``), every Ollama user who selects a non-default model is told to
    pull a model they never chose — on every retry, with no way forward.

    NOTE: this asserts Ollama IS validated. The DR copy table contracts the Ollama
    probe explicitly (C1 "Validating Ollama (local) host URL...", C6 "Cannot reach
    Ollama at {host}...", C7 "Ollama (local) host URL could not be verified."), so
    the ``credential_kind == "api_key"`` gate currently guarding the validate call
    must widen to cover the ``host`` kind.
    """
    observed: list[tuple[str, str, str]] = []
    saved: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "ollama"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "qwen2.5:7b"  # NOT the default (llama3.2)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "http://127.0.0.1:11434"
        return m

    def _validate(*, provider, api_key, model):
        observed.append((provider.value, api_key, model))
        return ValidationResult(ok=True, detail="Ollama host URL validated.")

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(llm_credential, "sync_env_values", lambda _values, **_kw: tmp_path / ".env")

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["model"] == "qwen2.5:7b"
    assert observed == [("ollama", "http://127.0.0.1:11434", "qwen2.5:7b")]
    assert all(model != "llama3.2" for _p, _k, model in observed)


# ---------------------------------------------------------------------------
# DELTA 4 (D2) — ONE shared recovery menu for BOTH failure sites.
#
# Contract (DR menu_contract):
#   _recovery_action(*, prompt, retry_label, retry_hint, escape)
#       -> "retry" | escape.value | "repick" | "cancel"
#   Exactly 3 rows, always in this order:
#       row 1  retry   (THE DEFAULT — value MUST be the literal "retry")
#       row 2  the per-site escape hatch (save_anyway | continue_unsaved)
#       row 3  _REPICK_CHOICE
# ---------------------------------------------------------------------------


def test_recovery_action_renders_three_rows_with_retry_as_the_default(monkeypatch) -> None:
    """The shared menu: 3 rows in a fixed order, row 1 is "retry" AND is the default.

    ``_choose(default=...)`` matches by ``Choice.value`` and raises ``ValueError``
    when it matches nothing, so ``default`` and ``choices[0].value`` must both be
    the literal string ``"retry"`` — this is a crash gate, not a cosmetic one.
    """
    recorded: dict[str, object] = {}

    def _fake_choose(prompt, choices, **kwargs):
        recorded["prompt"] = prompt
        recorded["choices"] = choices
        recorded["kwargs"] = kwargs
        return "retry"

    monkeypatch.setattr(llm_credential, "_choose", _fake_choose)

    escape = flow.Choice(
        value="save_anyway",
        label="Save anyway without validating",
        hint=(
            "Stores ANTHROPIC_API_KEY unverified — use when you are offline, "
            "proxied, or sure it is correct"
        ),
    )
    action = llm_credential._recovery_action(
        prompt="Anthropic API key could not be verified. What next?",
        retry_label="Re-enter the API key",
        retry_hint="Prompts for ANTHROPIC_API_KEY again",
        escape=escape,
    )

    assert action == "retry"
    assert recorded["prompt"] == "Anthropic API key could not be verified. What next?"

    choices = recorded["choices"]
    assert [choice.value for choice in choices] == ["retry", "save_anyway", "repick"]

    kwargs = recorded["kwargs"]
    assert kwargs["default"] == "retry"
    # The literal "retry" must be row 1's value or questionary raises ValueError.
    assert kwargs["default"] == choices[0].value
    # The WizardBack arm is dead code by contract: _choose must not be asked to
    # raise WizardBack here (it only does so when back_on_cancel=True).
    assert kwargs.get("back_on_cancel", False) is False

    assert choices[0].label == "Re-enter the API key"
    assert choices[0].hint == "Prompts for ANTHROPIC_API_KEY again"
    assert choices[1] is escape
    # Row 3's label is byte-identical to the pre-existing recovery menus.
    assert choices[2].label == "Pick a different LLM provider"
    assert choices[2].hint == "Returns to the provider and model picks"


def test_recovery_action_escape_returns_cancel_and_prints_setup_cancelled(
    monkeypatch, capsys
) -> None:
    """ESCAPE at either menu -> "cancel" -> "Setup cancelled." (X1).

    ESCAPE — not Ctrl-C — is the production trigger: ``_choose`` is called without
    ``back_on_cancel``, so an escaped select raises a BARE ``KeyboardInterrupt``.
    """

    def _fake_choose(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(llm_credential, "_choose", _fake_choose)

    action = llm_credential._recovery_action(
        prompt="ANTHROPIC_API_KEY could not be saved. What next?",
        retry_label="Retry saving to the system keychain",
        retry_hint="Run the steps above first, then retry",
        escape=flow.Choice(
            value="continue_unsaved",
            label="Continue without saving (this session only)",
            hint="Exports ANTHROPIC_API_KEY for this run — you must re-enter it next time",
        ),
    )

    assert action == "cancel"
    assert "Setup cancelled." in capsys.readouterr().out


def test_run_wizard_keyring_failure_continue_unsaved_exports_env_and_never_writes_dotenv(
    monkeypatch, tmp_path, capsys
) -> None:
    """The keychain escape hatch: export for this session ONLY, never leak to .env.

    ``continue_unsaved`` must set ``os.environ[provider.api_key_env]`` and nothing
    else. Writing the secret to ``.env`` is a secret-leak regression (#3291).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    select_prompts: list[str] = []
    menu_responses = iter(["continue_unsaved"])
    persist_attempts: list[tuple[str, str]] = []
    synced_env_values: list[dict[str, str]] = []
    env_path = tmp_path / ".env"

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    # Run the REAL sync against a tmp .env so the assertion below exercises the true
    # pop-then-re-apply path: sync_provider_env pops ANTHROPIC_API_KEY from os.environ,
    # and only the #3591 re-apply keeps it alive for the in-process shell handoff.
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kw: synced_env_values.append(dict(values)) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    # Exported for this process only, and it SURVIVES the real sync_provider_env pop so
    # the in-process shell handoff can read it (#3591). Without the re-apply this is "".
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-good"
    # ...and NEVER written to .env — assert against the real file the real sync wrote.
    env_written = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" not in env_written
    assert "sk-good" not in env_written
    assert all("ANTHROPIC_API_KEY" not in values for values in synced_env_values)
    assert all("sk-good" not in values.values() for values in synced_env_values)
    assert persist_attempts == [("anthropic", "sk-good")]
    output = capsys.readouterr().out
    assert "Using ANTHROPIC_API_KEY for this session only" in output


def test_run_wizard_keyring_failure_at_saved_provider_site_reaches_the_shared_menu(
    monkeypatch, tmp_path
) -> None:
    """Site B / saved-provider re-entry: keychain failure reaches the shared menu.

    "retry" re-runs the WRITE with the same value and does NOT re-prompt for the key.
    """
    select_prompts: list[str] = []
    password_prompts: list[str] = []
    menu_responses = iter(["retry"])
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False  # keep provider, keep model
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-7",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    # "retry" re-attempts the WRITE with the same value — no second key prompt.
    assert persist_attempts == [("anthropic", "sk-good"), ("anthropic", "sk-good")]
    assert len(password_prompts) == 1


def test_run_wizard_keyring_failure_at_legacy_migration_site_reaches_the_shared_menu(
    monkeypatch, tmp_path
) -> None:
    """Site C / legacy-key migration: keychain failure reaches the SAME shared menu.

    This site has no key prompt at all, which is exactly why the shared menu's
    "retry" verb re-runs the WRITE (not the prompt) — it is correct here too.
    """
    select_prompts: list[str] = []
    password_prompts: list[str] = []
    menu_responses = iter(["retry"])
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "must-not-be-prompted"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="stubbed"),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(_ui, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert "OPENAI_API_KEY could not be saved. What next?" in select_prompts
    assert persist_attempts == [("openai", "saved-secret"), ("openai", "saved-secret")]
    assert password_prompts == []  # the migration site never prompts


# ---------------------------------------------------------------------------
# DELTA 5 (OPEN-K) — the credential prompt must not offer a URL as an API key.
#
# The key prompt passes `default=provider.credential_default`. For azure-openai
# that default is the ENDPOINT placeholder "https://your-resource.openai.azure.com",
# and `_prompt_value` RETURNS THE DEFAULT ON EMPTY INPUT (_ui.py) — so a bare Enter
# silently persists a URL as the API key. Ollama's host default must survive.
# ---------------------------------------------------------------------------


def test_run_wizard_azure_empty_key_input_never_persists_the_endpoint_placeholder(
    monkeypatch, tmp_path
) -> None:
    """Enter on an empty Azure key prompt must NOT save the endpoint placeholder as the key."""
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)

    placeholder = flow.PROVIDER_BY_VALUE["azure-openai"].credential_default
    assert placeholder == "https://your-resource.openai.azure.com"  # sanity: the trap exists

    password_calls: list[dict[str, object]] = []
    password_asks = iter(["", "az-real-key"])  # bare Enter, then a real key
    validated_keys: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "azure-openai"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **kwargs):
        password_calls.append(kwargs)
        m = MagicMock()
        m.ask.return_value = next(password_asks)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "https://myres.openai.azure.com"
        return m

    def _validate(*, provider, api_key, model):
        validated_keys.append(api_key)
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    # The secret prompt must not pre-fill (and therefore must not return) the URL.
    assert all(call.get("default") == "" for call in password_calls)
    assert placeholder not in validated_keys
    assert all(value != placeholder for _provider, value in saved_llm_keys)
    assert validated_keys == ["az-real-key"]
    assert saved_llm_keys == [("azure-openai", "az-real-key")]


def test_run_wizard_ollama_host_prompt_keeps_its_localhost_default(monkeypatch, tmp_path) -> None:
    """OVER-FIX GUARD: clearing the prompt default must NOT strip Ollama's host default.

    Deliberately green today. It exists to go RED if DELTA 5 clears the default for
    every credential kind instead of only the non-"host" (secret) kinds: Ollama's
    host URL is plain config, and a bare Enter must still yield
    ``http://localhost:11434``.
    """
    text_calls: list[dict[str, object]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "ollama"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "llama3.2"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_text(*_args, **kwargs):
        text_calls.append(kwargs)
        m = MagicMock()
        m.ask.return_value = ""  # bare Enter -> must fall back to the offered default
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Ollama host URL validated."),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kw: synced_env_values.append(dict(values)) or (tmp_path / ".env"),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert any(call.get("default") == "http://localhost:11434" for call in text_calls)
    assert synced_env_values == [{"OLLAMA_HOST": "http://localhost:11434"}]


# ---------------------------------------------------------------------------
# DELTA 6 (OPEN-C) — the Summary screen must not contradict its own warning.
#
# `_credential_line_for_saved_summary` hardcodes "system keychain" for every
# non-cli kind, so stage 4/4 prints `credentials  system keychain` immediately
# after the wizard warned that the key was NOT saved.
# ---------------------------------------------------------------------------


def _summary_kwargs_spy(monkeypatch) -> dict[str, object]:
    """Record the kwargs `_render_saved_summary` is called with, then render for real."""
    recorded: dict[str, object] = {}
    real = flow._render_saved_summary

    def _spy(**kwargs):
        recorded.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(flow, "_render_saved_summary", _spy)
    return recorded


def test_run_wizard_summary_credential_line_after_continue_unsaved(monkeypatch, tmp_path) -> None:
    """Summary must read "not saved — re-enter next run" after continue_unsaved."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    summary = _summary_kwargs_spy(monkeypatch)
    menu_responses = iter(["continue_unsaved"])

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(_provider, _value, **_kwargs):
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        _ui,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert summary["credential_line"] == "not saved — re-enter next run"


def test_run_wizard_summary_credential_line_after_save_anyway(monkeypatch, tmp_path) -> None:
    """Summary must read "system keychain (unverified)" after save_anyway."""
    summary = _summary_kwargs_spy(monkeypatch)
    menu_responses = iter(["save_anyway"])

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-offline"
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(
            ok=False, detail="Validation request failed: Connection error."
        ),
    )
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(_ui, "save_api_key", _stub_save)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert summary["credential_line"] == "system keychain (unverified)"


# ---------------------------------------------------------------------------
# X2 — `except WizardBack` MUST precede `except KeyboardInterrupt`.
# ---------------------------------------------------------------------------


def test_prompt_validated_llm_credential_wizard_back_precedes_keyboard_interrupt(
    monkeypatch, capsys
) -> None:
    """EXCEPT-ORDER GATE. This test exists to go RED if the two arms are inverted.

    ``WizardBack`` SUBCLASSES ``KeyboardInterrupt`` (_ui.py), so an
    ``except KeyboardInterrupt`` arm placed FIRST silently swallows back-navigation
    and turns it into "Setup cancelled." Back-out at the key prompt must return
    "repick" (-> the provider picker) and must print nothing about cancelling.
    """

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = None  # ESCAPE at the key prompt -> WizardBack
        return m

    def _must_not_validate(**_kwargs):
        raise AssertionError("validation must not run after a back-out")

    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _must_not_validate)

    provider = flow.PROVIDER_BY_VALUE["anthropic"]
    outcome, _model = llm_credential._prompt_validated_llm_credential(
        provider, model="claude-opus-4-7", model_provider=provider
    )

    assert outcome == "repick"
    assert "Setup cancelled." not in capsys.readouterr().out


def test_run_wizard_azure_endpoint_reprompted_on_retry_after_validation_failure(
    monkeypatch, tmp_path
) -> None:
    """#3591 FIX 1 (RED): after an Azure endpoint+key validation FAILURE, choosing
    "retry" must re-prompt the ENDPOINT, not only the key.

    Repro of the dead-end: a wrong-but-valid endpoint is entered, then a key. The
    validator fails. The recovery menu's "retry" arm re-enters only the key; the next
    iteration sees ``azure_openai_endpoint_configured() == True`` (os.environ still
    holds the bad AZURE_OPENAI_BASE_URL) and short-circuits, so the second validation
    reuses the stale bad endpoint and the corrected endpoint the user would type is
    never asked for. The fix must let "retry" re-prompt the endpoint so a corrected
    URL reaches the second validation call.
    """
    # Ensure the Azure endpoint env starts unset AND is restored after the test, even
    # though the wizard writes it into os.environ mid-run.
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION")

    endpoint_prompts: list[str] = []
    endpoint_env_at_validation: list[str | None] = []
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    endpoint_values = iter(
        [
            "https://wrong-resource.openai.azure.com",
            "https://right-resource.openai.azure.com",
        ]
    )

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            # First (and only) validation failure -> re-enter, staying on Azure.
            m.ask.return_value = "retry"
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "azure-openai"
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "az-key"
        return m

    def _mock_text(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        endpoint_prompts.append(prompt)
        m = MagicMock()
        m.ask.return_value = next(endpoint_values)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        endpoint_env_at_validation.append(os.environ.get("AZURE_OPENAI_BASE_URL"))
        # First attempt fails; the retry (with a corrected endpoint) must succeed.
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Azure OpenAI rejected the request.")
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    # RED: the endpoint prompt is asked exactly ONCE today (the retry short-circuits on
    # the stale env). The fix must ask it a SECOND time so the correction is collected.
    assert len(endpoint_prompts) == 2, (
        f"expected the Azure endpoint to be re-prompted on retry, got "
        f"{len(endpoint_prompts)} endpoint prompt(s)"
    )
    # RED: the corrected endpoint must reach the second validation call. Today the
    # second probe reuses the stale wrong endpoint from os.environ.
    assert endpoint_env_at_validation == [
        "https://wrong-resource.openai.azure.com",
        "https://right-resource.openai.azure.com",
    ]
    assert validator_calls == [("azure-openai", "az-key"), ("azure-openai", "az-key")]
    assert saved_llm_keys == [("azure-openai", "az-key")]
    assert exit_code == 0


def test_run_wizard_azure_endpoint_reprompted_on_repick_then_reselect(
    monkeypatch, tmp_path
) -> None:
    """#3591 FIX 1 (RED): after an Azure validation FAILURE, choosing "Pick a different
    LLM provider" (repick) and then re-selecting Azure must re-prompt the endpoint.

    Today nothing pops AZURE_OPENAI_BASE_URL between wizard iterations
    (``sync_provider_env`` runs only after the loop breaks), so on the re-selection
    ``azure_openai_endpoint_configured()`` is still True and the endpoint prompt is
    short-circuited to the stale bad URL. The fix must re-prompt the endpoint on the
    repick arm so a corrected URL reaches the second validation call.
    """
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION")

    endpoint_prompts: list[str] = []
    endpoint_env_at_validation: list[str | None] = []
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    provider_responses = iter(["azure-openai", "azure-openai"])
    endpoint_values = iter(
        [
            "https://wrong-resource.openai.azure.com",
            "https://right-resource.openai.azure.com",
        ]
    )

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            # First (and only) validation failure -> bail to the provider picker.
            m.ask.return_value = "repick"
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "az-key"
        return m

    def _mock_text(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        endpoint_prompts.append(prompt)
        m = MagicMock()
        m.ask.return_value = next(endpoint_values)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        endpoint_env_at_validation.append(os.environ.get("AZURE_OPENAI_BASE_URL"))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Azure OpenAI rejected the request.")
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        _ui,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    # RED: today the endpoint is prompted ONCE; the re-selected Azure short-circuits on
    # the stale AZURE_OPENAI_BASE_URL. The fix must re-prompt it after repick+reselect.
    assert len(endpoint_prompts) == 2, (
        f"expected the Azure endpoint to be re-prompted after repick+reselect, got "
        f"{len(endpoint_prompts)} endpoint prompt(s)"
    )
    # RED: the corrected endpoint must reach the second validation call.
    assert endpoint_env_at_validation == [
        "https://wrong-resource.openai.azure.com",
        "https://right-resource.openai.azure.com",
    ]
    assert exit_code == 0


def test_credential_line_for_saved_summary_ollama_host_unverified() -> None:
    """#3591 FIX 3 (RED): an Ollama host saved via "Save anyway" (unverified) is written
    to .env, not the keyring — the summary must name .env, never the system keychain.

    ``credential_kind == "host"`` values go through the .env branch of
    ``_persist_llm_credential`` (``sync_env_values`` + ``os.environ``), never the
    keyring. The honest summary line must reflect that. Today the function falls into
    the ``credential_kind != "cli"`` block and returns "system keychain (unverified)",
    which lies about where the credential landed.
    """
    ollama = flow.PROVIDER_BY_VALUE["ollama"]
    line = llm_credential._credential_line_for_saved_summary(ollama, credential_state="unverified")
    assert "keychain" not in line.lower(), (
        f"an Ollama host lives in .env, not the keychain; got {line!r}"
    )
    assert ".env" in line, f"the summary must name .env for a host credential; got {line!r}"
    # The line must still disclose that the saved host was not verified.
    assert "unverified" in line.lower(), f"the unverified state must be disclosed; got {line!r}"


def test_credential_line_for_saved_summary_ollama_host_verified() -> None:
    """#3591 FIX 3 (RED): a verified Ollama host is still a .env credential, so the
    summary must name .env and must not claim the system keychain.
    """
    ollama = flow.PROVIDER_BY_VALUE["ollama"]
    line = llm_credential._credential_line_for_saved_summary(ollama)
    assert "keychain" not in line.lower(), (
        f"an Ollama host lives in .env, not the keychain; got {line!r}"
    )
    assert ".env" in line, f"the summary must name .env for a host credential; got {line!r}"
    # A verified host must not be labelled unverified.
    assert "unverified" not in line.lower(), (
        f"a verified host must not read unverified; got {line!r}"
    )


@pytest.mark.parametrize("stale_mode", ["aha", "banana", ""])
def test_run_wizard_falls_back_when_saved_mode_is_not_offered(monkeypatch, stale_mode) -> None:
    """A saved mode that is no longer offered must default to 'focused'.

    Saved defaults outlive the modes they name — a value persisted by an older
    build, or a mode removed in a refactor, must not be passed to the chooser as
    a default it cannot render.
    """
    # Arrange
    monkeypatch.setattr(
        flow,
        "_local_defaults",
        lambda: {"provider": None, "model": "", "wizard_mode": stale_mode, "auth_method": None},
    )
    seen: dict[str, object] = {}

    def _capture_choose(_prompt, _choices, default=None, **_kwargs):
        seen["default"] = default
        raise KeyboardInterrupt  # stop the wizard once the default is known

    monkeypatch.setattr(flow, "_choose", _capture_choose)
    monkeypatch.setattr(flow, "_render_header", lambda: None)
    monkeypatch.setattr(flow, "_step_header", lambda *_a, **_k: None)

    # Act
    with pytest.raises(KeyboardInterrupt):
        flow.run_wizard()

    # Assert
    assert seen["default"] == "focused"
