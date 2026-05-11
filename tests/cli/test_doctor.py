from __future__ import annotations

from unittest.mock import MagicMock

from app.cli.commands import doctor


def test_check_python_version_ok(monkeypatch) -> None:
    monkeypatch.setattr(doctor.platform, "python_version", lambda: "3.12.7")
    monkeypatch.setattr(doctor.sys, "version_info", (3, 12, 7, "final", 0))

    ok, detail = doctor._check_python_version()

    assert ok is True
    assert detail == "Python 3.12.7"


def test_check_python_version_too_old(monkeypatch) -> None:
    monkeypatch.setattr(doctor.platform, "python_version", lambda: "3.10.14")
    monkeypatch.setattr(doctor.sys, "version_info", (3, 10, 14, "final", 0))

    ok, detail = doctor._check_python_version()

    assert ok is False
    assert "Python 3.10.14" in detail
    assert "requires >= 3.11" in detail


def test_check_env_file_counts_non_comment_keys(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "OPENAI_API_KEY=test-key",
                "LLM_PROVIDER=openai",
                "   ",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(env_file))

    ok, detail = doctor._check_env_file()

    assert ok is True
    assert str(env_file) in detail
    assert "(2 keys)" in detail


def test_check_env_file_missing(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(env_file))

    ok, detail = doctor._check_env_file()

    assert ok is False
    assert detail == f"{env_file} not found"


def test_check_llm_provider_not_set(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    ok, detail = doctor._check_llm_provider()
    assert ok is False
    assert "not set" in detail


def test_check_llm_provider_hosted_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "has_llm_api_key", lambda _env_var: False)
    ok, detail = doctor._check_llm_provider()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in detail
    assert "env or keyring" in detail


def test_check_llm_provider_hosted_keyring_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        doctor,
        "has_llm_api_key",
        lambda env_var: env_var == "GEMINI_API_KEY",
    )

    ok, detail = doctor._check_llm_provider()

    assert ok is True
    assert detail == "provider=gemini"


def test_check_llm_provider_non_secret_env_stays_env_only(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    def _raise_if_called(_env_var: str) -> bool:
        raise AssertionError("non-secret provider env should not use keyring lookup")

    monkeypatch.setattr(doctor, "has_llm_api_key", _raise_if_called)

    ok, detail = doctor._check_llm_provider()

    assert ok is False
    assert "AWS_DEFAULT_REGION" in detail
    assert "not set" in detail


def test_check_llm_provider_claude_code_ready(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/claude",
        logged_in=True,
        detail="Authenticated via Claude subscription.",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "claude-code" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is True
    assert "CLI ready" in detail


def test_check_llm_provider_claude_code_auth_unclear(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/claude",
        logged_in=None,
        detail="claude auth status failed: unknown command",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "claude-code" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is False
    assert "auth status unclear" in detail


def test_check_llm_provider_cli_branch_follows_registry_not_hardcoded_ids(monkeypatch) -> None:
    """Any LLM_PROVIDER listed in CLI_PROVIDER_REGISTRY gets the CLI probe path."""
    monkeypatch.setenv("LLM_PROVIDER", "hypothetical-cli")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/hypothetical",
        logged_in=True,
        detail="CLI OK.",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "hypothetical-cli" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is True
    assert "CLI ready" in detail


def test_check_llm_provider_gemini_cli_ready(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/gemini",
        logged_in=True,
        detail="Authenticated via Gemini CLI.",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "gemini-cli" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is True
    assert "CLI ready" in detail


def test_check_integrations_store_missing(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "integrations.json"
    monkeypatch.setattr("app.integrations.store.STORE_PATH", store_path)
    monkeypatch.setattr(
        "app.integrations.store.list_integrations", lambda: [{"service": "grafana"}]
    )

    ok, detail = doctor._check_integrations()

    assert ok is False
    assert str(store_path) in detail
    assert "opensre integrations setup" in detail


def test_check_integrations_empty_store(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "integrations.json"
    store_path.write_text('{"version": 2, "integrations": []}\n', encoding="utf-8")
    monkeypatch.setattr("app.integrations.store.STORE_PATH", store_path)
    monkeypatch.setattr("app.integrations.store.list_integrations", lambda: [])

    ok, detail = doctor._check_integrations()

    assert ok is False
    assert detail == "no integrations configured"


def test_check_integrations_reports_configured_services(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "integrations.json"
    store_path.write_text('{"version": 2, "integrations": []}\n', encoding="utf-8")
    monkeypatch.setattr("app.integrations.store.STORE_PATH", store_path)
    monkeypatch.setattr(
        "app.integrations.store.list_integrations",
        lambda: [{"service": "grafana"}, {"service": "datadog"}],
    )

    ok, detail = doctor._check_integrations()

    assert ok is True
    assert detail == "2 configured: grafana, datadog"


def test_check_version_freshness_skips_release_compare_for_local_dev(monkeypatch) -> None:
    fetch_latest_version = MagicMock(return_value="9.9.9")
    monkeypatch.setattr(doctor, "get_version", lambda: "1.2.3")
    monkeypatch.setattr(
        "app.cli.support.update.development_install_doctor_version_detail",
        lambda c: f"{c} (editable install; skipped comparing to latest release)",
    )
    monkeypatch.setattr("app.cli.support.update._fetch_latest_version", fetch_latest_version)

    ok, detail = doctor._check_version_freshness()

    assert ok is True
    assert detail == "1.2.3 (editable install; skipped comparing to latest release)"
    fetch_latest_version.assert_not_called()


def test_check_version_freshness_up_to_date(monkeypatch) -> None:
    fetch_latest_version = MagicMock(return_value="1.2.3")
    is_update_available = MagicMock(return_value=False)
    monkeypatch.setattr(doctor, "get_version", lambda: "1.2.3")
    monkeypatch.setattr(
        "app.cli.support.update.development_install_doctor_version_detail", lambda _c: None
    )
    monkeypatch.setattr("app.cli.support.update._fetch_latest_version", fetch_latest_version)
    monkeypatch.setattr("app.cli.support.update._is_update_available", is_update_available)

    ok, detail = doctor._check_version_freshness()

    assert ok is True
    assert detail == "1.2.3 (up to date)"
    fetch_latest_version.assert_called_once_with()
    is_update_available.assert_called_once_with("1.2.3", "1.2.3")


def test_check_version_freshness_update_available(monkeypatch) -> None:
    fetch_latest_version = MagicMock(return_value="1.3.0")
    is_update_available = MagicMock(return_value=True)
    monkeypatch.setattr(doctor, "get_version", lambda: "1.2.3")
    monkeypatch.setattr(
        "app.cli.support.update.development_install_doctor_version_detail", lambda _c: None
    )
    monkeypatch.setattr("app.cli.support.update._fetch_latest_version", fetch_latest_version)
    monkeypatch.setattr("app.cli.support.update._is_update_available", is_update_available)

    ok, detail = doctor._check_version_freshness()

    assert ok is False
    assert "current=1.2.3, latest=1.3.0" in detail
    assert "opensre update" in detail
    fetch_latest_version.assert_called_once_with()
    is_update_available.assert_called_once_with("1.2.3", "1.3.0")


def test_check_version_freshness_soft_fails_on_fetch_error(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "get_version", lambda: "1.2.3")
    monkeypatch.setattr(
        "app.cli.support.update.development_install_doctor_version_detail", lambda _c: None
    )

    def _raise() -> str:
        raise RuntimeError("rate limited")

    monkeypatch.setattr("app.cli.support.update._fetch_latest_version", _raise)

    ok, detail = doctor._check_version_freshness()

    assert ok is True
    assert detail == "1.2.3 (could not check: rate limited)"
